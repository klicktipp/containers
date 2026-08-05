"""Prometheus exporter for Certified Senders Alliance monitoring data."""

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
dkim_email_volume_gauge = Gauge(
    "csa_dkim_email_volume", "Email volume per DKIM domain", ["domain"]
)
dkim_missing_gauge = Gauge(
    "csa_dkim_missing", "Missing DKIM keys per DKIM domain", ["domain"]
)
dkim_above_csa_limit_gauge = Gauge(
    "csa_dkim_above_csa_limit",
    "1 if the DKIM domain is above a CSA KPI limit, else 0",
    ["domain"],
)

# Header-From Domain KPI Metrics
from_email_volume_gauge = Gauge(
    "csa_from_domain_email_volume", "Email volume per header-from domain", ["domain"]
)
from_spam_trap_hits_gauge = Gauge(
    "csa_from_domain_spam_trap_hits",
    "Spam trap hits per header-from domain",
    ["domain"],
)
from_dkim_errors_gauge = Gauge(
    "csa_from_domain_dkim_errors", "DKIM errors per header-from domain", ["domain"]
)
from_dkim_missing_gauge = Gauge(
    "csa_from_domain_dkim_missing",
    "Missing DKIM keys per header-from domain",
    ["domain"],
)
from_aligned_mails_gauge = Gauge(
    "csa_from_domain_aligned_mails", "Aligned mails per header-from domain", ["domain"]
)
from_non_aligned_gauge = Gauge(
    "csa_from_domain_non_aligned",
    "Non-aligned mails per header-from domain",
    ["domain"],
)
from_spam_click_ratio_gauge = Gauge(
    "csa_from_domain_spam_click_ratio",
    "Spam complaint ratio per header-from domain",
    ["domain"],
)
from_above_csa_limit_gauge = Gauge(
    "csa_from_domain_above_csa_limit",
    "1 if the header-from domain is above a CSA KPI limit, else 0",
    ["domain"],
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
ip_email_volume_gauge = Gauge("csa_ip_email_volume", "Email volume per IP", ["ip"])
ip_above_csa_limit_gauge = Gauge(
    "csa_ip_above_csa_limit", "1 if the IP is above a CSA KPI limit, else 0", ["ip"]
)

# Detailed Spam Complaint Rate Metrics
spam_complaint_rate_gauge = Gauge(
    "csa_spam_complaint_rate",
    "CSA spam complaint rate by entity and statistic",
    ["scope", "entity", "statistic"],
)
spam_complaint_volume_gauge = Gauge(
    "csa_spam_complaint_total_volume",
    "Email volume underlying the CSA spam complaint rate",
    ["scope", "entity"],
)
alignment_gauge = Gauge(
    "csa_alignment_mails",
    "CSA mail alignment counts by entity and result",
    ["scope", "entity", "result"],
)
dkim_detail_gauge = Gauge(
    "csa_dkim_detail",
    "CSA DKIM error count and underlying volume by entity",
    ["scope", "entity", "statistic"],
)
legal_complaints_gauge = Gauge(
    "csa_legal_complaints",
    "Absolute CSA legal complaint or case count",
    ["scope", "brand", "kind"],
)

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


def _load_complaint_lookback_days() -> int:
    """Read the legal-complaint lookback with a safe default."""

    raw_days = os.getenv("CSA_COMPLAINT_LOOKBACK_DAYS", "30")
    try:
        days = int(raw_days)
    except ValueError:
        logger.warning(
            "Invalid CSA_COMPLAINT_LOOKBACK_DAYS value '%s'; using 30 days.",
            raw_days,
        )
        return 30
    if days <= 0:
        logger.warning("CSA_COMPLAINT_LOOKBACK_DAYS must be positive; using 30 days.")
        return 30
    return days


# Configuration with Environment Variables
API_URL = os.getenv(
    "CSA_API_URL", "https://monitor.certified-senders.org/api/v1"
).rstrip("/")
API_TOKEN = os.getenv("CSA_API_TOKEN", "").strip()
API_ID = os.getenv("CSA_API_ID", "").strip()
API_SECRET = os.getenv("CSA_API_SECRET", "").strip()
REQUEST_TIMEOUT = _load_timeout()
COMPLAINT_LOOKBACK_DAYS = _load_complaint_lookback_days()


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
AUTHORIZATION_HEADER = _build_authorization_header()
if AUTHORIZATION_HEADER:
    session.headers["Authorization"] = AUTHORIZATION_HEADER
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
    dkim_email_volume_gauge.clear()
    dkim_missing_gauge.clear()
    dkim_above_csa_limit_gauge.clear()

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
        dkim_email_volume_gauge.labels(domain=domain).set(
            _to_float(metric.get("email_volume"))
        )
        dkim_missing_gauge.labels(domain=domain).set(
            _to_int(metric.get("dkim_missing"))
        )
        dkim_above_csa_limit_gauge.labels(domain=domain).set(
            1 if metric.get("above_csa_limit") is True else 0
        )


def fetch_kpi_from_domain_metrics(date_str: Optional[str]) -> None:
    """Fetch and update KPI metrics grouped by header-from domain."""

    gauges = (
        from_email_volume_gauge,
        from_spam_trap_hits_gauge,
        from_dkim_errors_gauge,
        from_dkim_missing_gauge,
        from_aligned_mails_gauge,
        from_non_aligned_gauge,
        from_spam_click_ratio_gauge,
        from_above_csa_limit_gauge,
    )
    for gauge in gauges:
        gauge.clear()

    if not date_str:
        return

    data = _get_json("/stat/kpi/fromdomain", {"date": date_str})
    if not data:
        return

    for metric in data:
        domain = metric.get("from_domain")
        if not domain:
            continue
        from_email_volume_gauge.labels(domain=domain).set(
            _to_float(metric.get("email_volume"))
        )
        from_spam_trap_hits_gauge.labels(domain=domain).set(
            _to_int(metric.get("spam_traps"))
        )
        from_dkim_errors_gauge.labels(domain=domain).set(
            _to_int(metric.get("dkim_errors"))
        )
        from_dkim_missing_gauge.labels(domain=domain).set(
            _to_int(metric.get("dkim_missing"))
        )
        from_aligned_mails_gauge.labels(domain=domain).set(
            _to_int(metric.get("aligned"))
        )
        from_non_aligned_gauge.labels(domain=domain).set(
            _to_int(metric.get("non_aligned"))
        )
        from_spam_click_ratio_gauge.labels(domain=domain).set(
            _to_float(metric.get("spam_click_ratio"))
        )
        from_above_csa_limit_gauge.labels(domain=domain).set(
            1 if metric.get("above_csa_limit") is True else 0
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
    ip_email_volume_gauge.clear()
    ip_above_csa_limit_gauge.clear()

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
        ip_email_volume_gauge.labels(ip=ip).set(_to_float(metric.get("email_volume")))
        ip_above_csa_limit_gauge.labels(ip=ip).set(
            1 if metric.get("above_csa_limit") is True else 0
        )


def fetch_spam_complaint_rates(date_str: Optional[str]) -> None:
    """Fetch detailed complaint-rate statistics for all supported entity scopes."""

    spam_complaint_rate_gauge.clear()
    spam_complaint_volume_gauge.clear()
    if not date_str:
        return

    for scope in ("global", "dkimdomain", "fromdomain", "ip"):
        data = _get_json(f"/stat/spamclickrate/{scope}", {"date": date_str})
        if not data:
            continue

        entities = {"global": data} if scope == "global" else data
        if not isinstance(entities, dict):
            continue
        for entity, values in entities.items():
            if not isinstance(values, dict):
                continue
            for statistic in ("min", "avg", "max"):
                spam_complaint_rate_gauge.labels(
                    scope=scope, entity=str(entity), statistic=statistic
                ).set(_to_float(values.get(statistic)))
            spam_complaint_volume_gauge.labels(scope=scope, entity=str(entity)).set(
                _to_float(values.get("total_volume"))
            )


def fetch_alignment_details(date_str: Optional[str]) -> None:
    """Fetch alignment breakdowns, including strict and relaxed alignment."""

    alignment_gauge.clear()
    if not date_str:
        return

    for scope in ("global", "dkimdomain", "fromdomain", "ip"):
        data = _get_json(f"/stat/aligned/{scope}", {"date": date_str})
        if not data:
            continue
        entities = {"global": data} if scope == "global" else data
        if not isinstance(entities, dict):
            continue
        for entity, values in entities.items():
            if not isinstance(values, dict):
                continue
            for result in (
                "aligned",
                "non_aligned",
                "simple_relaxed",
                "simple_strict",
                "total_volume",
            ):
                alignment_gauge.labels(
                    scope=scope, entity=str(entity), result=result
                ).set(_to_float(values.get(result)))


def fetch_dkim_details(date_str: Optional[str]) -> None:
    """Fetch exact DKIM error counts and their underlying mail volumes."""

    dkim_detail_gauge.clear()
    if not date_str:
        return

    for scope in ("global", "dkimdomain", "fromdomain", "ip"):
        data = _get_json(f"/stat/dkimerrors/{scope}", {"date": date_str})
        if not data:
            continue
        entities = {"global": data} if scope == "global" else data
        if not isinstance(entities, dict):
            continue
        for entity, values in entities.items():
            if not isinstance(values, dict):
                continue
            for statistic in ("errors", "total_volume"):
                dkim_detail_gauge.labels(
                    scope=scope, entity=str(entity), statistic=statistic
                ).set(_to_float(values.get(statistic)))


def fetch_legal_complaints(date_str: Optional[str]) -> None:
    """Fetch absolute legal CSA complaint and case counts without PII."""

    legal_complaints_gauge.clear()
    if not date_str:
        return

    for kind in ("complaints", "cases"):
        global_amount = _get_text(f"/csa_complaints/global/{kind}", {"date": date_str})
        if global_amount is not None:
            legal_complaints_gauge.labels(scope="global", brand="", kind=kind).set(
                _to_int(global_amount)
            )

        brands = _get_json(
            f"/csa_complaints/brands/{kind}",
            {"date": date_str, "days": str(COMPLAINT_LOOKBACK_DAYS)},
        )
        if not isinstance(brands, list):
            continue
        for entry in brands:
            brand = entry.get("brand")
            if not brand:
                continue
            legal_complaints_gauge.labels(
                scope="brand", brand=str(brand), kind=kind
            ).set(_to_int(entry.get("amount")))


@app.route("/healthz")
def healthz():
    """Return the process health status."""

    return "OK", 200


@app.route("/livez")
def livez():
    """Return the process liveness status."""

    return "OK", 200


@app.route("/metrics")
def metrics():
    """Refresh CSA data and return Prometheus metrics."""

    date_str = get_latest_data_date()

    fetch_global_metrics(date_str)
    fetch_kpi_dkim_metrics(date_str)
    fetch_kpi_from_domain_metrics(date_str)
    fetch_kpi_ip_metrics(date_str)
    fetch_spam_complaint_rates(date_str)
    fetch_alignment_details(date_str)
    fetch_dkim_details(date_str)
    fetch_legal_complaints(date_str)
    fetch_inbox_placement_deviation(date_str)
    fetch_spam_complaint_rate_deviation(date_str)

    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "9100"))
    host = os.getenv("HOST", "0.0.0.0")  # nosec B104 - container listener
    threads = int(os.getenv("WAITRESS_THREADS", "4"))
    serve(app, host=host, port=port, threads=threads)
