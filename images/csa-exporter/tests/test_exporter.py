import importlib.util
import os
import sys
import types
import unittest
from base64 import b64encode
from pathlib import Path
from unittest import mock


class FakeValue:
    def __init__(self):
        self.current = 0

    def get(self):
        return self.current


class FakeGaugeChild:
    def __init__(self, gauge, labels):
        self._gauge = gauge
        self._labels = labels
        self._value = FakeValue()

    def set(self, value):
        self._value.current = value
        self._gauge.children[self._labels] = self


class FakeGauge:
    def __init__(self, name, description, labelnames=()):
        self.name = name
        self.description = description
        self.labelnames = tuple(labelnames)
        self.children = {}
        self._value = FakeValue()

    def labels(self, **kwargs):
        labels = tuple((label, kwargs[label]) for label in self.labelnames)
        if labels not in self.children:
            self.children[labels] = FakeGaugeChild(self, labels)
        return self.children[labels]

    def set(self, value):
        self._value.current = value

    def clear(self):
        self.children = {}


class FakeCounterChild:
    def __init__(self):
        self.value = 0

    def inc(self):
        self.value += 1


class FakeCounter:
    def __init__(self, name, description, labelnames=()):
        self.name = name
        self.description = description
        self.labelnames = tuple(labelnames)
        self.children = {}

    def labels(self, **kwargs):
        labels = tuple((label, kwargs[label]) for label in self.labelnames)
        if labels not in self.children:
            self.children[labels] = FakeCounterChild()
        return self.children[labels]


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
        *,
        text=None,
        status_code=200,
        json_data=None,
        url="https://example.invalid",
        mimetype=None,
    ):
        self.text = body if text is None else text
        self.status_code = status_code
        self._json_data = json_data
        self.url = url
        self.mimetype = mimetype

    def json(self):
        if self._json_data is None:
            raise ValueError("missing json")
        return self._json_data


class FakeSession:
    def __init__(self):
        self.headers = {}

    def get(self, _url, params=None, timeout=None):
        del params, timeout
        return FakeResponse()


class FakeWaitress(types.ModuleType):
    def __init__(self):
        super().__init__("waitress")
        self.calls = []

    def serve(self, app, host=None, port=None, threads=None):
        self.calls.append(
            {"app": app, "host": host, "port": port, "threads": threads}
        )


def load_exporter_module():
    fake_requests = types.ModuleType("requests")
    fake_requests.Session = FakeSession
    fake_requests.RequestException = Exception
    fake_requests.Response = FakeResponse

    fake_flask = types.ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.Response = FakeResponse

    fake_prometheus = types.ModuleType("prometheus_client")
    fake_prometheus.CONTENT_TYPE_LATEST = "text/plain"
    fake_prometheus.Counter = FakeCounter
    fake_prometheus.Gauge = FakeGauge
    fake_prometheus.generate_latest = lambda: b"metrics"
    fake_waitress = FakeWaitress()

    sys.modules["requests"] = fake_requests
    sys.modules["flask"] = fake_flask
    sys.modules["prometheus_client"] = fake_prometheus
    sys.modules["waitress"] = fake_waitress

    module_path = (
        Path(__file__).resolve().parents[1] / "rootfs/usr/local/bin/exporter.py"
    )
    spec = importlib.util.spec_from_file_location("csa_exporter_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._fake_waitress = fake_waitress
    return module


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        self.exporter = load_exporter_module()

    def tearDown(self):
        self._env.stop()

    def test_load_timeout_defaults_for_invalid_values(self):
        with mock.patch.dict(os.environ, {"CSA_API_TIMEOUT": "invalid"}, clear=True):
            self.assertEqual(self.exporter._load_timeout(), 10)

        with mock.patch.dict(os.environ, {"CSA_API_TIMEOUT": "0"}, clear=True):
            self.assertEqual(self.exporter._load_timeout(), 10)

    def test_build_authorization_header_prefers_explicit_header(self):
        with mock.patch.dict(
            self.exporter.os.environ,
            {"CSA_API_TOKEN": "abc", "CSA_API_ID": "name", "CSA_API_SECRET": "secret"},
            clear=True,
        ):
            self.exporter.API_TOKEN = self.exporter.os.getenv("CSA_API_TOKEN", "").strip()
            self.exporter.API_ID = self.exporter.os.getenv("CSA_API_ID", "").strip()
            self.exporter.API_SECRET = self.exporter.os.getenv("CSA_API_SECRET", "").strip()
            self.assertEqual(self.exporter._build_authorization_header(), "ApiKey abc")

    def test_build_authorization_header_uses_base64_name_and_key(self):
        with mock.patch.dict(
            self.exporter.os.environ,
            {"CSA_API_ID": "name", "CSA_API_SECRET": "secret"},
            clear=True,
        ):
            self.exporter.API_TOKEN = self.exporter.os.getenv("CSA_API_TOKEN", "").strip()
            self.exporter.API_ID = self.exporter.os.getenv("CSA_API_ID", "").strip()
            self.exporter.API_SECRET = self.exporter.os.getenv("CSA_API_SECRET", "").strip()
            expected = b64encode(b"name:secret").decode("ascii")
            self.assertEqual(self.exporter._build_authorization_header(), f"ApiKey {expected}")

    def test_normalize_date_rejects_invalid_input(self):
        self.assertEqual(self.exporter._normalize_date(' "2026-06-15" '), "2026-06-15")
        self.assertIsNone(self.exporter._normalize_date("15-06-2026"))

    def test_metrics_endpoint_updates_expected_gauges(self):
        json_by_path = {
            "/stat/dkimerrors/global": {"errors": 7},
            "/stat/kpi/dkimdomain": [
                {
                    "dkim_domain": "example.com",
                    "aligned": 12,
                    "dkim_errors": 1,
                    "non_aligned": 3,
                    "spam_click_ratio": 0.25,
                    "spam_traps": 2,
                }
            ],
            "/stat/kpi/ip": [
                {
                    "ip": "192.0.2.10",
                    "aligned": 9,
                    "dkim_errors": 2,
                    "dkim_missing": 1,
                    "non_aligned": 4,
                    "spam_click_ratio": 0.5,
                    "spam_traps": 3,
                }
            ],
            "/stat/anomaly/iprdeviation": [{"date": "2026-06-15", "iprdev": 1.5}],
            "/stat/anomaly/scrdeviation": [{"date": "2026-06-15", "scrdev": 0.75}],
        }
        text_by_path = {
            "/stat": "2026-06-15",
            "/stat/spamtrap/global": "11",
        }

        def fake_request(path, params=None):
            del params
            if path in json_by_path:
                return FakeResponse(json_data=json_by_path[path], url=f"https://example.invalid{path}")
            if path in text_by_path:
                return FakeResponse(text=text_by_path[path], url=f"https://example.invalid{path}")
            self.fail(f"unexpected path {path}")

        with mock.patch.object(self.exporter, "_request_api", side_effect=fake_request):
            response = self.exporter.metrics()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.exporter.spam_global_trap_hits_gauge._value.get(), 11)
        self.assertEqual(self.exporter.dkim_global_dkim_errors_gauge._value.get(), 7)
        self.assertEqual(self.exporter.ipr_deviation_gauge._value.get(), 1.5)
        self.assertEqual(self.exporter.scr_deviation_gauge._value.get(), 0.75)
        self.assertEqual(
            self.exporter.dkim_aligned_mails_gauge.labels(domain="example.com")._value.get(),
            12,
        )
        self.assertEqual(
            self.exporter.ip_dkim_missing_gauge.labels(ip="192.0.2.10")._value.get(),
            1,
        )

    def test_main_uses_waitress_configuration(self):
        with mock.patch.dict(
            self.exporter.os.environ,
            {"HOST": "127.0.0.1", "PORT": "9200", "WAITRESS_THREADS": "8"},
            clear=True,
        ):
            self.exporter.serve(
                self.exporter.app,
                host=self.exporter.os.getenv("HOST", "0.0.0.0"),
                port=int(self.exporter.os.getenv("PORT", "9100")),
                threads=int(self.exporter.os.getenv("WAITRESS_THREADS", "4")),
            )

        self.assertEqual(len(self.exporter._fake_waitress.calls), 1)
        self.assertEqual(
            self.exporter._fake_waitress.calls[0],
            {
                "app": self.exporter.app,
                "host": "127.0.0.1",
                "port": 9200,
                "threads": 8,
            },
        )
