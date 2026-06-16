from __future__ import annotations

import base64
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests
from flask import Flask, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from waitress import serve

# Enhanced Logging Configuration
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# DKIM Domain Metrics
dkim_aligned_mails_gauge = Gauge(
    "csa_dkim_aligned_mails", "Aligned mails per DKIM domain", ["domain"]
)
dkim_errors_gauge = Gauge("csa_dkim_errors", "DKIM errors per DKIM domain", ["domain"])
dkim_spam_trap_hits_gauge = Gauge(
    "csa_dkim_spam_trap_hits", "Spam trap hits per DKIM domain", ["domain"]
)
dkim_non_aligned_gauge = Gauge(
    "csa_dkim_non_aligned", "Non-aligned mails per DKIM domain", ["domain"]
)
dkim_spam_click_ratio_gauge = Gauge(
    "csa_dkim_spam_click_ratio", "Spam click ratio per DKIM domain", ["domain"]
)

# Global Metrics
spam_global_trap_hits_gauge = Gauge(
    "csa_global_spam_trap_hits", "Global spam trap hits"
)
dkim_global_dkim_errors_gauge = Gauge("csa_global_dkim_errors", "Global DKIM errors")

# IPR Deviation Metric (Inbox Placement Rate)
ipr_deviation_gauge = Gauge("csa_ipr_deviation", "IPR deviation for specific date")

# SCR Deviation Metric (Spam Complaint Rate)
scr_deviation_gauge = Gauge("csa_scr_deviation", "SCR deviation for specific date")

# IP KPI Metrics
ip_aligned_mails_gauge = Gauge("csa_ip_aligned_mails", "Aligned mails per IP", ["ip"])
ip_dkim_errors_gauge = Gauge("csa_ip_dkim_errors", "DKIM errors per IP", ["ip"])
ip_dkim_missing_gauge = Gauge("csa_ip_dkim_missing", "Missing DKIM keys per IP", ["ip"])
ip_non_aligned_gauge = Gauge("csa_ip_non_aligned", "Non-aligned mails per IP", ["ip"])
ip_spam_click_ratio_gauge = Gauge(
    "csa_ip_spam_click_ratio", "Spam click ratio per IP", ["ip"]
)
ip_spam_trap_hits_gauge = Gauge("csa_ip_spam_trap_hits", "Spam traps per IP", ["ip"])

# Error and Performance Tracking Metrics
api_request_failures = Counter(
    "csa_api_request_failures", "Number of API request failures", ["endpoint"]
)
api_request_latency = Gauge(
    "csa_api_request_latency_seconds", "Latency of API requests", ["endpoint"]
)


def _load_timeout() -> int:
    """Read the timeout configuration from the environment with sane fallbacks."""

    raw_timeout = os.getenv("CSA_API_TIMEOUT", "10")
    try:
        timeout = int(raw_timeout)
    except ValueError:
        logger.warning(
            "Invalid CSA_API_TIMEOUT value '%s', falling back to 10 seconds.",
            raw_timeout,
        )
        return 10

    if timeout <= 0:
        logger.warning(
            "CSA_API_TIMEOUT must be positive, received %s. Using 10 seconds instead.",
            timeout,
        )
        return 10

    return timeout


# Configuration with Environment Variables
API_URL = os.getenv(
    "CSA_API_URL", "https://monitor.certified-senders.org/api/v1"
).rstrip("/")
API_TOKEN = os.getenv("CSA_API_TOKEN", "").strip()
API_ID = os.getenv("CSA_API_ID", "").strip()
API_SECRET = os.getenv("CSA_API_SECRET", "").strip()
REQUEST_TIMEOUT = _load_timeout()


def _build_authorization_header() -> str:
    """Return the Authorization header value for the configured auth mode."""

    if API_TOKEN:
        return f"ApiKey {API_TOKEN}"

    if API_ID and API_SECRET:
        auth_bytes = f"{API_ID}:{API_SECRET}".encode("utf-8")
        return f"ApiKey {base64.b64encode(auth_bytes).decode('ascii')}"

    return ""


session = requests.Session()
session.headers.update({"User-Agent": "CSA Metrics Exporter/1.0"})
authorization_header = _build_authorization_header()
if authorization_header:
    session.headers["Authorization"] = authorization_header
else:
    logger.warning(
        "No CSA API authentication is configured. Set CSA_API_TOKEN to the "
        "base64 token from the CSA UI, or set CSA_API_ID together with "
        "CSA_API_SECRET."
    )


def _normalize_date(raw_date: str) -> Optional[str]:
    """Return a YYYY-MM-DD date string without quotes or whitespace."""

    candidate = raw_date.strip().strip('"')
    if not candidate:
        return None

    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        logger.error("Invalid date format received from API: %s", raw_date)
        return None

    return candidate


def _request_api(
    path: str, params: Optional[Dict[str, str]] = None
) -> Optional[requests.Response]:
    """Make a GET request against the CSA API and record telemetry."""

    url = f"{API_URL}{path}"
    start_time = time.perf_counter()
    try:
        response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:  # pragma: no cover - telemetry
        latency = time.perf_counter() - start_time
        api_request_latency.labels(endpoint=path).set(latency)
        logger.error("Request to %s failed: %s", url, exc)
        api_request_failures.labels(endpoint=path).inc()
        return None

    latency = time.perf_counter() - start_time
    api_request_latency.labels(endpoint=path).set(latency)

    if response.status_code >= 400:
        logger.error(
            "Unexpected HTTP status %s while querying %s", response.status_code, url
        )
        api_request_failures.labels(endpoint=path).inc()
        return None

    return response


def _get_json(path: str, params: Optional[Dict[str, str]] = None) -> Optional[Any]:
    """Fetch JSON payload from the CSA API."""

    response = _request_api(path, params)
    if response is None:
        return None

    try:
        return response.json()
    except ValueError:
        logger.error("Received invalid JSON from %s", response.url)
        api_request_failures.labels(endpoint=path).inc()
        return None


def _get_text(path: str, params: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Fetch raw text payload from the CSA API."""

    response = _request_api(path, params)
    if response is None:
        return None

    return response.text.strip()


def _to_int(value: Any) -> int:
    """Best-effort conversion to int returning 0 on failure."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    """Best-effort conversion to float returning 0.0 on failure."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_latest_data_date() -> Optional[str]:
    """Fetch the latest available data date."""

    logger.debug("Fetching the latest available data date.")
    raw_date = _get_text("/stat")
    if raw_date is None:
        return None

    date = _normalize_date(raw_date)
    if not date:
        return None

    logger.info("Latest available data date: %s", date)
    return date


def fetch_kpi_dkim_metrics(date_str: Optional[str]) -> None:
    """Fetch and update KPI DKIM domain metrics."""

    dkim_aligned_mails_gauge.clear()
    dkim_errors_gauge.clear()
    dkim_non_aligned_gauge.clear()
    dkim_spam_click_ratio_gauge.clear()
    dkim_spam_trap_hits_gauge.clear()

    if not date_str:
        return

    data = _get_json("/stat/kpi/dkimdomain", {"date": date_str})
    if not data:
        return

    for metric in data:
        domain = metric.get("dkim_domain")
        if not domain:
            continue

        dkim_aligned_mails_gauge.labels(domain=domain).set(
            _to_int(metric.get("aligned"))
        )
        dkim_errors_gauge.labels(domain=domain).set(_to_int(metric.get("dkim_errors")))
        dkim_non_aligned_gauge.labels(domain=domain).set(
            _to_int(metric.get("non_aligned"))
        )
        dkim_spam_click_ratio_gauge.labels(domain=domain).set(
            _to_float(metric.get("spam_click_ratio"))
        )
        dkim_spam_trap_hits_gauge.labels(domain=domain).set(
            _to_int(metric.get("spam_traps"))
        )


def fetch_global_metrics(date_str: Optional[str]) -> None:
    """Fetch and update global metrics."""

    spam_global_trap_hits_gauge.set(0)
    dkim_global_dkim_errors_gauge.set(0)

    if not date_str:
        return

    spam_traps = _get_text("/stat/spamtrap/global", {"date": date_str})
    if spam_traps and spam_traps.isdigit():
        spam_global_trap_hits_gauge.set(int(spam_traps))

    dkim_errors = _get_json("/stat/dkimerrors/global", {"date": date_str})
    if isinstance(dkim_errors, dict):
        dkim_global_dkim_errors_gauge.set(_to_int(dkim_errors.get("errors")))


def _set_deviation_metric(
    path: str, date_str: Optional[str], metric_name: str, gauge: Gauge, value_key: str
) -> None:
    """Fetch deviation metrics (IPR/SCR) and set the provided gauge."""

    gauge.set(0.0)
    if not date_str:
        return

    data = _get_json(path, {"date": date_str})
    if not data:
        return

    for entry in data:
        entry_date_raw = entry.get("date")
        if not entry_date_raw:
            continue

        normalized_date = _normalize_date(str(entry_date_raw))
        if normalized_date != date_str:
            continue

        value = _to_float(entry.get(value_key))
        gauge.set(value)
        logger.debug("Setting %s for %s to %s", metric_name, date_str, value)
        return

    logger.debug("No %s data found for %s", metric_name, date_str)


def fetch_inbox_placement_deviation(date_str: Optional[str]) -> None:
    """Fetch and update inbox placement deviation metric for a specific date."""

    _set_deviation_metric(
        "/stat/anomaly/iprdeviation",
        date_str,
        "IPR deviation",
        ipr_deviation_gauge,
        "iprdev",
    )


def fetch_spam_complaint_rate_deviation(date_str: Optional[str]) -> None:
    """Fetch and update spam complaint rate deviation metric for a specific date."""

    _set_deviation_metric(
        "/stat/anomaly/scrdeviation",
        date_str,
        "SCR deviation",
        scr_deviation_gauge,
        "scrdev",
    )


def fetch_kpi_ip_metrics(date_str: Optional[str]) -> None:
    """Fetch and update IP KPI metrics."""

    ip_aligned_mails_gauge.clear()
    ip_dkim_errors_gauge.clear()
    ip_dkim_missing_gauge.clear()
    ip_non_aligned_gauge.clear()
    ip_spam_click_ratio_gauge.clear()
    ip_spam_trap_hits_gauge.clear()

    if not date_str:
        return

    data = _get_json("/stat/kpi/ip", {"date": date_str})
    if not data:
        return

    for metric in data:
        ip = metric.get("ip")
        if not ip:
            continue

        ip_aligned_mails_gauge.labels(ip=ip).set(_to_int(metric.get("aligned")))
        ip_dkim_errors_gauge.labels(ip=ip).set(_to_int(metric.get("dkim_errors")))
        ip_dkim_missing_gauge.labels(ip=ip).set(_to_int(metric.get("dkim_missing")))
        ip_non_aligned_gauge.labels(ip=ip).set(_to_int(metric.get("non_aligned")))
        ip_spam_click_ratio_gauge.labels(ip=ip).set(
            _to_float(metric.get("spam_click_ratio"))
        )
        ip_spam_trap_hits_gauge.labels(ip=ip).set(_to_int(metric.get("spam_traps")))


@app.route("/healthz")
def healthz():
    return "OK", 200


@app.route("/livez")
def livez():
    return "OK", 200


@app.route("/metrics")
def metrics():
    date_str = get_latest_data_date()

    fetch_global_metrics(date_str)
    fetch_kpi_dkim_metrics(date_str)
    fetch_kpi_ip_metrics(date_str)
    fetch_inbox_placement_deviation(date_str)
    fetch_spam_complaint_rate_deviation(date_str)

    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 9100))
    host = os.getenv("HOST", "0.0.0.0")
    threads = int(os.getenv("WAITRESS_THREADS", "4"))
    serve(app, host=host, port=port, threads=threads)
