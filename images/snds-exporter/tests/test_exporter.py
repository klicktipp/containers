import importlib.util
import sys
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
    def __init__(self, body="", headers=None):
        self.text = body
        self.headers = headers or {}


class FakeSession:
    def __init__(self):
        self.headers = {}


def load_exporter_module():
    fake_requests = types.ModuleType("requests")
    fake_requests.Session = FakeSession
    fake_requests.RequestException = Exception
    fake_requests.Response = FakeResponse

    fake_flask = types.ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.Response = FakeResponse

    fake_prom = types.ModuleType("prometheus_client")
    fake_prom.CONTENT_TYPE_LATEST = "text/plain"
    fake_prom.Gauge = FakeGauge
    fake_prom.generate_latest = lambda: b""

    sys.modules["requests"] = fake_requests
    sys.modules["flask"] = fake_flask
    sys.modules["prometheus_client"] = fake_prom

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


if __name__ == "__main__":
    unittest.main()
