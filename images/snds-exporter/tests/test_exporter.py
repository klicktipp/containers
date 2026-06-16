import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


class FakeGaugeChild:
    def __init__(self, gauge, labels):
        self._gauge = gauge
        self._labels = labels

    def set(self, value):
        self._gauge.samples[self._labels] = value


class FakeGauge:
    def __init__(self, name, description, labelnames=()):
        self.name = name
        self.description = description
        self.labelnames = tuple(labelnames)
        self.samples = {}
        self.value = None

    def labels(self, **kwargs):
        labels = tuple((label, kwargs[label]) for label in self.labelnames)
        return FakeGaugeChild(self, labels)

    def set(self, value):
        self.value = value

    def clear(self):
        self.samples = {}


class FakeFlask:
    def __init__(self, name):
        self.name = name

    def route(self, _path):
        def decorator(func):
            return func

        return decorator


class FakeResponse:
    def __init__(
        self,
        body="",
        headers=None,
        status_code=200,
        ok=True,
        mimetype=None,
        json_data=None,
        **_kwargs,
    ):
        self.text = body
        self.headers = headers or {}
        self.status_code = status_code
        self.ok = ok
        self.mimetype = mimetype
        self._json_data = json_data

    def raise_for_status(self):
        if not self.ok or self.status_code >= 400:
            raise Exception(f"{self.status_code} error")

    def json(self):
        if self._json_data is not None:
            return self._json_data
        if self.text:
            return {"text": self.text}
        return {}


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.responses = []
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, verify=None):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "timeout": timeout,
                "verify": verify,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse()


def load_exporter_module():
    fake_requests = types.ModuleType("requests")
    fake_requests.Session = FakeSession
    fake_requests.RequestException = Exception
    fake_requests.Response = FakeResponse
    fake_requests.post = lambda *args, **kwargs: FakeResponse()
    fake_requests.patch = lambda *args, **kwargs: FakeResponse()

    fake_flask = types.ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.Response = FakeResponse
    fake_flask.request = types.SimpleNamespace(args={})

    fake_prom = types.ModuleType("prometheus_client")
    fake_prom.CONTENT_TYPE_LATEST = "text/plain"
    fake_prom.Gauge = FakeGauge
    fake_prom.generate_latest = lambda: b""

    fake_waitress = types.ModuleType("waitress")
    fake_waitress.serve = lambda *args, **kwargs: None

    sys.modules["requests"] = fake_requests
    sys.modules["flask"] = fake_flask
    sys.modules["prometheus_client"] = fake_prom
    sys.modules["waitress"] = fake_waitress

    module_path = (
        Path(__file__).resolve().parents[1] / "rootfs/usr/local/bin/exporter.py"
    )
    spec = importlib.util.spec_from_file_location("test_exporter_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXPORTER = load_exporter_module()


class ExporterTests(unittest.TestCase):
    def setUp(self):
        EXPORTER._auth_state_loaded = False
        EXPORTER._access_token_cache = ""
        EXPORTER._access_token_expires_at = None
        EXPORTER._refresh_token_cache = ""
        EXPORTER._last_fetch_epoch = 0.0
        EXPORTER._last_fetch_success = False
        EXPORTER._update_gauges(
            "Ip Address,Activity period,RCPT commands,DATA commands,Message recipients,"
            "Filter result,Complaint rate,Trap message period,Trap hits,JMR P1 Sender,Comments\n"
            "192.0.2.1,6/1/2026 1:00 AM,10,8,8,GREEN,<0.1%,6/1/2026 2:00 AM,0,,\n"
        )
        for gauge_name in (
            "activity_period_timestamp_gauge",
            "rcpt_commands_gauge",
            "email_volume_gauge",
            "message_recipients_gauge",
            "trap_message_period_timestamp_gauge",
            "trap_hits_gauge",
            "complaint_rate_gauge",
            "overall_status_info_gauge",
            "jmrp1_sender_present_gauge",
            "comments_present_gauge",
        ):
            getattr(EXPORTER, gauge_name).clear()

    def test_parses_header_based_csv(self):
        csv_content = (
            "Ip Address,Activity period,RCPT commands,DATA commands,Message recipients,"
            "Filter result,Complaint rate,Trap message period,Trap hits,JMR P1 Sender,Comments\n"
            "192.0.2.10,6/1/2026 1:00 AM,10,8,7,GREEN,<0.1%,6/1/2026 2:00 AM,0,yes,ok\n"
        )

        processed = EXPORTER._update_gauges(csv_content)

        self.assertEqual(processed, 1)
        self.assertEqual(
            EXPORTER.rcpt_commands_gauge.samples[(("ip", "192.0.2.10"),)], 10
        )
        self.assertEqual(
            EXPORTER.email_volume_gauge.samples[(("ip", "192.0.2.10"),)], 8
        )
        self.assertEqual(
            EXPORTER.message_recipients_gauge.samples[(("ip", "192.0.2.10"),)], 7
        )
        self.assertEqual(EXPORTER.trap_hits_gauge.samples[(("ip", "192.0.2.10"),)], 0)
        self.assertEqual(
            EXPORTER.complaint_rate_gauge.samples[(("ip", "192.0.2.10"),)], 0.1
        )
        self.assertEqual(
            EXPORTER.overall_status_info_gauge.samples[
                (("ip", "192.0.2.10"), ("status", "green"))
            ],
            1,
        )
        self.assertIsNotNone(
            EXPORTER.activity_period_timestamp_gauge.samples[(("ip", "192.0.2.10"),)]
        )
        self.assertIsNotNone(
            EXPORTER.trap_message_period_timestamp_gauge.samples[
                (("ip", "192.0.2.10"),)
            ]
        )
        self.assertEqual(
            EXPORTER.jmrp1_sender_present_gauge.samples[(("ip", "192.0.2.10"),)], 1
        )
        self.assertEqual(
            EXPORTER.comments_present_gauge.samples[(("ip", "192.0.2.10"),)], 1
        )

    def test_parses_headerless_csv_by_value_inference(self):
        csv_content = "192.0.2.20,6/1/2026 1:00 AM,111,222,333,YELLOW,<0.1%,6/1/2026 2:00 AM,4,,note\n"

        processed = EXPORTER._update_gauges(csv_content)

        self.assertEqual(processed, 1)
        self.assertEqual(
            EXPORTER.rcpt_commands_gauge.samples[(("ip", "192.0.2.20"),)], 111
        )
        self.assertEqual(
            EXPORTER.email_volume_gauge.samples[(("ip", "192.0.2.20"),)], 333
        )
        self.assertEqual(
            EXPORTER.message_recipients_gauge.samples[(("ip", "192.0.2.20"),)], 222
        )
        self.assertEqual(EXPORTER.trap_hits_gauge.samples[(("ip", "192.0.2.20"),)], 4)
        self.assertEqual(
            EXPORTER.overall_status_info_gauge.samples[
                (("ip", "192.0.2.20"), ("status", "yellow"))
            ],
            1,
        )

    def test_complaint_rate_parses_capped_value(self):
        self.assertEqual(EXPORTER._parse_complaint_rate("<0.1%"), 0.1)

    def test_parse_timestamp_parses_expected_snds_format(self):
        self.assertIsNotNone(EXPORTER._parse_timestamp("6/1/2026 1:00 AM"))

    def test_unknown_response_sample_logging_is_safe(self):
        EXPORTER._log_unknown_response_sample(
            '{"unexpected": "shape"}', ValueError("broken format")
        )

    def test_parses_ip_status_json(self):
        processed = EXPORTER._update_ip_status_gauges(
            '[{"beginningIpAddress":"192.0.2.10","endingIpAddress":"192.0.2.20","blocked":"true","reason":"Blocked due to complaints"}]'
        )

        self.assertEqual(processed, 1)
        self.assertEqual(
            EXPORTER.ip_status_blocked_gauge.samples[
                (("range_start", "192.0.2.10"), ("range_end", "192.0.2.20"))
            ],
            1,
        )

    def test_parses_headerless_ip_status_csv(self):
        processed = EXPORTER._update_ip_status_gauges(
            "176.119.155.0,176.119.155.255,True,Blocked due to user complaints or other evidence of spamming\n"
        )

        self.assertEqual(processed, 1)
        self.assertEqual(
            EXPORTER.ip_status_blocked_gauge.samples[
                (("range_start", "176.119.155.0"), ("range_end", "176.119.155.255"))
            ],
            1,
        )

    def test_refresh_access_token_uses_cached_refresh_token(self):
        original_cache_file = EXPORTER.SNDS_TOKEN_CACHE_FILE
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        original_post = EXPORTER.requests.post
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                cache_path = Path(temp_dir) / "token-cache.json"
                token_path = Path(temp_dir) / "access-token"
                cache_path.write_text(
                    '{"access_token_expires_at": 1, "refresh_token": "refresh-value"}',
                    encoding="utf-8",
                )
                EXPORTER.SNDS_TOKEN_CACHE_FILE = str(cache_path)
                EXPORTER.SNDS_ACCESS_TOKEN_FILE = str(token_path)

                def fake_post(*_args, **_kwargs):
                    return FakeResponse(
                        json_data={
                            "access_token": "renewed-token",
                            "refresh_token": "refresh-value-2",
                            "expires_in": 3600,
                        }
                    )

                EXPORTER.requests.post = fake_post

                token = EXPORTER._refresh_access_token()

                self.assertEqual(token, "renewed-token")
                self.assertEqual(token_path.read_text(encoding="utf-8").strip(), "renewed-token")
                self.assertIn("refresh-value-2", cache_path.read_text(encoding="utf-8"))
        finally:
            EXPORTER.SNDS_TOKEN_CACHE_FILE = original_cache_file
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file
            EXPORTER.requests.post = original_post

    def test_normalize_column_name_handles_camel_case(self):
        self.assertEqual(EXPORTER._normalize_column_name("ipAddress"), "ip address")
        self.assertEqual(
            EXPORTER._normalize_column_name("trapMessagePeriod"),
            "trap message period",
        )

    def test_build_request_uses_rest_api_with_bearer_token(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        original_rest_api_url = EXPORTER.REST_API_URL
        original_rest_api_date = EXPORTER.REST_API_DATE
        original_rest_api_ip = EXPORTER.REST_API_IP
        original_default_rest_api_dates = EXPORTER._default_rest_api_dates
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = "token-value"
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = ""
            EXPORTER.REST_API_URL = (
                "https://substrate.office.com/ip-domain-management-snds/api/report/data"
            )
            EXPORTER.REST_API_DATE = ""
            EXPORTER.REST_API_IP = ""
            EXPORTER._default_rest_api_dates = lambda: ["2026-06-09"]

            data_url, params, headers = EXPORTER._build_rest_request_candidates()[0]

            self.assertEqual(
                data_url,
                "https://substrate.office.com/ip-domain-management-snds/api/report/data/2026-06-09",
            )
            self.assertEqual(params, {})
            self.assertEqual(headers, {"Authorization": "Bearer token-value"})
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file
            EXPORTER.REST_API_URL = original_rest_api_url
            EXPORTER.REST_API_DATE = original_rest_api_date
            EXPORTER.REST_API_IP = original_rest_api_ip
            EXPORTER._default_rest_api_dates = original_default_rest_api_dates

    def test_build_request_appends_rest_api_date_and_ip(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        original_rest_api_url = EXPORTER.REST_API_URL
        original_rest_api_date = EXPORTER.REST_API_DATE
        original_rest_api_ip = EXPORTER.REST_API_IP
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = "token-value"
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = ""
            EXPORTER.REST_API_URL = (
                "https://substrate.office.com/ip-domain-management-snds/api/report/data"
            )
            EXPORTER.REST_API_DATE = "2026-12-31"
            EXPORTER.REST_API_IP = "192.0.2.4"

            data_url, params, headers = EXPORTER._build_rest_request_candidates()[0]

            self.assertEqual(
                data_url,
                "https://substrate.office.com/ip-domain-management-snds/api/report/data/2026-12-31/192.0.2.4",
            )
            self.assertEqual(params, {})
            self.assertEqual(headers, {"Authorization": "Bearer token-value"})
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file
            EXPORTER.REST_API_URL = original_rest_api_url
            EXPORTER.REST_API_DATE = original_rest_api_date
            EXPORTER.REST_API_IP = original_rest_api_ip

    def test_load_access_token_reads_file(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = ""
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as token_file:
                token_file.write("file-token\n")
                token_file.flush()
                EXPORTER.SNDS_ACCESS_TOKEN_FILE = token_file.name

                self.assertEqual(EXPORTER._load_access_token(), "file-token")
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file

    def test_load_token_cache_ignores_empty_file(self):
        original_cache_file = EXPORTER.SNDS_TOKEN_CACHE_FILE
        try:
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as cache_file:
                cache_file.write("")
                cache_file.flush()
                EXPORTER.SNDS_TOKEN_CACHE_FILE = cache_file.name

                self.assertEqual(EXPORTER._load_token_cache(), {})
        finally:
            EXPORTER.SNDS_TOKEN_CACHE_FILE = original_cache_file

    def test_load_token_cache_ignores_invalid_json(self):
        original_cache_file = EXPORTER.SNDS_TOKEN_CACHE_FILE
        try:
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as cache_file:
                cache_file.write("not-json")
                cache_file.flush()
                EXPORTER.SNDS_TOKEN_CACHE_FILE = cache_file.name

                self.assertEqual(EXPORTER._load_token_cache(), {})
        finally:
            EXPORTER.SNDS_TOKEN_CACHE_FILE = original_cache_file

    def test_default_rest_api_date_uses_yesterday_in_utc(self):
        class FakeDateTime(EXPORTER.dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 6, 10, 3, 15, tzinfo=EXPORTER.dt.timezone.utc)

        original_datetime = EXPORTER.dt.datetime
        try:
            EXPORTER.dt.datetime = FakeDateTime
            self.assertEqual(EXPORTER._default_rest_api_date(), "2026-06-09")
            self.assertEqual(
                EXPORTER._default_rest_api_dates()[:3],
                ["2026-06-09", "2026-06-08", "2026-06-07"],
            )
        finally:
            EXPORTER.dt.datetime = original_datetime

    def test_fetch_snds_data_looks_back_for_latest_available_rest_date(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_rest_api_date = EXPORTER.REST_API_DATE
        original_default_rest_api_dates = EXPORTER._default_rest_api_dates
        original_status_api_url = EXPORTER.STATUS_API_URL
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = "token-value"
            EXPORTER.REST_API_DATE = ""
            EXPORTER.STATUS_API_URL = "https://substrate.office.com/ip-domain-management-snds/api/report/status/ip"
            EXPORTER._default_rest_api_dates = lambda: ["2026-06-09", "2026-06-08"]
            EXPORTER._session.responses = [
                FakeResponse(status_code=404, ok=False),
                FakeResponse(status_code=404, ok=False),
                FakeResponse(
                    body='[{"ipAddress":"192.0.2.10","dataCommands":1,"filterResult":"GREEN"}]'
                ),
                FakeResponse(
                    body='[{"beginningIpAddress":"192.0.2.10","endingIpAddress":"192.0.2.20","blocked":"true","reason":"Blocked due to complaints"}]'
                ),
            ]

            EXPORTER.fetch_snds_data(force=True)

            self.assertEqual(
                EXPORTER._session.calls[-1]["url"],
                "https://substrate.office.com/ip-domain-management-snds/api/report/status/ip",
            )
            self.assertEqual(
                EXPORTER._session.calls[-2]["url"],
                "https://substrate.office.com/ip-domain-management-snds/api/report/data/2026-06-08",
            )
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.REST_API_DATE = original_rest_api_date
            EXPORTER._default_rest_api_dates = original_default_rest_api_dates
            EXPORTER.STATUS_API_URL = original_status_api_url

    def test_request_with_rest_fallback_retries_with_trailing_slash(self):
        EXPORTER._session.responses = [
            FakeResponse(status_code=404, ok=False),
            FakeResponse(
                body='[{"ipAddress":"192.0.2.10","dataCommands":1,"filterResult":"GREEN"}]'
            ),
        ]

        response = EXPORTER._request_with_rest_fallback(
            "https://substrate.office.com/ip-domain-management-snds/api/report/data",
            {},
            {"Authorization": "Bearer token"},
        )

        self.assertEqual(
            [call["url"] for call in EXPORTER._session.calls[-2:]],
            [
                "https://substrate.office.com/ip-domain-management-snds/api/report/data",
                "https://substrate.office.com/ip-domain-management-snds/api/report/data/",
            ],
        )
        self.assertEqual(response.status_code, 200)

    def test_metrics_query_params_override_rest_request_path(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_request_args = EXPORTER.request.args
        original_status_api_url = EXPORTER.STATUS_API_URL
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = "token-value"
            EXPORTER.STATUS_API_URL = "https://substrate.office.com/ip-domain-management-snds/api/report/status/ip"
            EXPORTER.request.args = {"date": "2026-12-31", "ip": "192.0.2.4"}
            EXPORTER._session.responses = [
                FakeResponse(
                    body='[{"ipAddress":"192.0.2.10","dataCommands":1,"filterResult":"GREEN"}]'
                ),
                FakeResponse(
                    body='[{"beginningIpAddress":"192.0.2.10","endingIpAddress":"192.0.2.20","blocked":"true","reason":"Blocked due to complaints"}]'
                ),
            ]

            EXPORTER.metrics()

            self.assertEqual(
                EXPORTER._session.calls[-2]["url"],
                "https://substrate.office.com/ip-domain-management-snds/api/report/data/2026-12-31/192.0.2.4",
            )
            self.assertEqual(
                EXPORTER._session.calls[-1]["url"],
                "https://substrate.office.com/ip-domain-management-snds/api/report/status/ip",
            )
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.request.args = original_request_args
            EXPORTER.STATUS_API_URL = original_status_api_url

    def test_healthz_is_ready_without_auth_material(self):
        body, status = EXPORTER.healthz()

        self.assertEqual(body, "OK")
        self.assertEqual(status, 200)

    def test_healthz_is_ready_before_first_fetch_with_auth_material(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = "token-value"
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = ""

            body, status = EXPORTER.healthz()

            self.assertEqual(body, "OK")
            self.assertEqual(status, 200)
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file

    def test_healthz_returns_503_after_failed_fetch(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        try:
            EXPORTER.SNDS_ACCESS_TOKEN = "token-value"
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = ""
            EXPORTER._last_fetch_epoch = 1.0
            EXPORTER._last_fetch_success = False

            body, status = EXPORTER.healthz()

            self.assertEqual(body, "SNDS data not yet available")
            self.assertEqual(status, 503)
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file

    def test_has_auth_material_reloads_updated_files_after_empty_bootstrap(self):
        original_token = EXPORTER.SNDS_ACCESS_TOKEN
        original_token_file = EXPORTER.SNDS_ACCESS_TOKEN_FILE
        original_cache_file = EXPORTER.SNDS_TOKEN_CACHE_FILE
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                token_path = Path(temp_dir) / "access-token"
                cache_path = Path(temp_dir) / "token-cache.json"
                token_path.write_text("", encoding="utf-8")
                cache_path.write_text("", encoding="utf-8")
                EXPORTER.SNDS_ACCESS_TOKEN = ""
                EXPORTER.SNDS_ACCESS_TOKEN_FILE = str(token_path)
                EXPORTER.SNDS_TOKEN_CACHE_FILE = str(cache_path)

                self.assertFalse(EXPORTER._has_auth_material())

                token_path.write_text("new-token\n", encoding="utf-8")
                cache_path.write_text(
                    '{"access_token_expires_at": 9999999999, "refresh_token": "refresh-value"}',
                    encoding="utf-8",
                )

                self.assertTrue(EXPORTER._has_auth_material())
        finally:
            EXPORTER.SNDS_ACCESS_TOKEN = original_token
            EXPORTER.SNDS_ACCESS_TOKEN_FILE = original_token_file
            EXPORTER.SNDS_TOKEN_CACHE_FILE = original_cache_file


if __name__ == "__main__":
    unittest.main()
