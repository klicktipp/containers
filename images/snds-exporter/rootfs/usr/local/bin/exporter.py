import csv
import datetime as dt
import ipaddress
import json
import logging
import os
import threading
import time
from io import StringIO
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

import requests
from flask import Flask, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from waitress import serve

# Configure logging once per process
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# SNDS Metrics
rcpt_commands_gauge = Gauge("snds_rcpt_commands", "RCPT commands from SNDS", ["ip"])
activity_period_timestamp_gauge = Gauge(
    "snds_activity_period_timestamp",
    "Activity period timestamp from SNDS as Unix time",
    ["ip"],
)
email_volume_gauge = Gauge("snds_email_volume", "DATA field from SNDS", ["ip"])
message_recipients_gauge = Gauge(
    "snds_message_recipients", "Message recipients from SNDS", ["ip"]
)
trap_message_period_timestamp_gauge = Gauge(
    "snds_trap_message_period_timestamp",
    "Trap message period timestamp from SNDS as Unix time",
    ["ip"],
)
trap_hits_gauge = Gauge("snds_trap_hits", "Trap hits from SNDS", ["ip"])
complaint_rate_gauge = Gauge("snds_complaint_rate", "Complaint rate from SNDS", ["ip"])
overall_status_gauge = Gauge(
    "snds_overall_status",
    "Deprecated numeric overall status per IP",
    ["ip", "status"],
)
overall_status_info_gauge = Gauge(
    "snds_overall_status_info", "One-hot overall status per IP", ["ip", "status"]
)
jmrp1_sender_present_gauge = Gauge(
    "snds_jmrp1_sender_present",
    "1 if the SNDS row contains a JMRP1 sender value",
    ["ip"],
)
comments_present_gauge = Gauge(
    "snds_comments_present", "1 if the SNDS row contains comments", ["ip"]
)
fetch_success_gauge = Gauge(
    "snds_last_fetch_success", "1 if the most recent SNDS fetch succeeded"
)
fetch_timestamp_gauge = Gauge(
    "snds_last_successful_fetch_timestamp",
    "Unix timestamp of the most recent successful SNDS fetch",
)
fetch_duration_gauge = Gauge(
    "snds_last_fetch_duration_seconds", "Duration of the most recent SNDS fetch"
)
fetch_parse_error_gauge = Gauge(
    "snds_last_fetch_parse_error",
    "1 if the most recent SNDS fetch failed due to response parsing",
)

# Configuration sourced from environment
API_URL = os.getenv(
    "API_URL", "https://sendersupport.olc.protection.outlook.com/snds/data.aspx"
)
API_KEY = os.getenv("API_KEY", "")
AUTOMATED_DATA_ACCESS_URL = os.getenv("AUTOMATED_DATA_ACCESS_URL", "")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "300"))
VERIFY_TLS = os.getenv("VERIFY_TLS", "true").lower() not in {"0", "false", "no"}
USER_AGENT = os.getenv("USER_AGENT", "kt-snds-exporter/1.0")
DEBUG_UNKNOWN_RESPONSES = os.getenv("DEBUG_UNKNOWN_RESPONSES", "false").lower() in {
    "1",
    "true",
    "yes",
}
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "9100"))

status_numeric_mapping = {"green": 1, "yellow": 2, "red": 3}
FIELD_ALIASES = {
    "ip": {"beginning ip address", "ip", "ip address"},
    "activity_period": {"activity period"},
    "rcpt_commands": {"rcpt commands"},
    "email_volume": {
        "data",
        "data commands",
        "email volume",
        "mail volume",
        "message count",
    },
    "message_recipients": {"message recipients"},
    "overall_status": {"overall status", "filter result", "status", "ip status"},
    "complaint_rate": {"complaint rate", "complaint percent", "complaints"},
    "trap_message_period": {"trap message period"},
    "trap_hits": {"trap hits", "spam trap hits", "trap count"},
    "jmrp1_sender": {"jmr p1 sender", "jmrp1 sender"},
    "comments": {"comments"},
}

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_lock = threading.Lock()
_last_fetch_epoch = 0.0
_last_fetch_success = False


def _normalize_column_name(value: str) -> str:
    return " ".join(
        value.lstrip("\ufeff")
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
        .split()
    )


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _is_status_value(value: str) -> bool:
    return value.strip().lower() in status_numeric_mapping


def _looks_like_percentage(value: str) -> bool:
    stripped = value.strip()
    return stripped.endswith("%") or stripped.startswith("<")


def _looks_like_datetime_text(value: str) -> bool:
    stripped = value.strip().lower()
    if not stripped:
        return False
    return any(token in stripped for token in ("/", " am", " pm", ":", "-"))


def _parse_timestamp(value: str) -> Optional[float]:
    value = value.strip()
    if not value:
        return None

    for fmt in (
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue

    return None


def _parse_int(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_complaint_rate(value: str) -> Optional[float]:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value.lstrip("<").rstrip("%"))
    except ValueError:
        return None


def _extract_data_url_and_params() -> tuple[str, dict[str, str]]:
    data_url = AUTOMATED_DATA_ACCESS_URL or API_URL
    if not data_url:
        raise ValueError("Neither AUTOMATED_DATA_ACCESS_URL nor API_URL is set.")

    params = dict(parse_qsl(urlsplit(data_url).query, keep_blank_values=True))
    if API_KEY and "key" not in params:
        params["key"] = API_KEY

    return data_url, params


def _validate_response(response: requests.Response) -> None:
    content_type = response.headers.get("Content-Type", "").lower()
    body_start = response.text.lstrip()[:128].lower()

    if "text/html" in content_type or body_start.startswith("<"):
        raise ValueError(
            "SNDS returned HTML instead of CSV. The link may require login, may have expired, or the endpoint has changed."
        )

    if "sign in to your microsoft account" in body_start:
        raise ValueError("SNDS returned a sign-in page instead of report data.")


def _log_unknown_response_sample(response_text: str, error: Exception) -> None:
    if not DEBUG_UNKNOWN_RESPONSES:
        return

    stripped = response_text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(response_text)
            if isinstance(payload, dict):
                preview = sorted(payload.keys())[:10]
            elif payload and isinstance(payload, list) and isinstance(payload[0], dict):
                preview = sorted(payload[0].keys())[:10]
            else:
                preview = [type(payload).__name__]
            logger.warning("SNDS parse debug keys after %s: %s", error, preview)
            return
        except Exception:
            pass

    sample_lines = [
        line.strip() for line in response_text.splitlines()[:5] if line.strip()
    ]
    logger.warning("SNDS parse debug lines after %s: %s", error, sample_lines)


def _build_sniffer_sample(csv_content: str) -> str:
    return "\n".join(line for line in csv_content.splitlines()[:10] if line.strip())


def _csv_reader(csv_content: str) -> csv.reader:
    sample = _build_sniffer_sample(csv_content)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.reader(StringIO(csv_content), dialect)


def _resolve_column_indexes(header: list[str]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    normalized = [_normalize_column_name(column) for column in header]

    for logical_name, aliases in FIELD_ALIASES.items():
        for index, column_name in enumerate(normalized):
            if column_name in aliases:
                indexes[logical_name] = index
                break

    missing = {"ip", "email_volume", "overall_status"} - set(indexes)
    if missing:
        raise ValueError(
            "SNDS CSV format is unsupported. Missing columns: "
            + ", ".join(sorted(missing))
        )

    return indexes


def _find_header_row(
    rows: list[list[str]],
) -> tuple[Optional[int], Optional[dict[str, int]]]:
    for index, row in enumerate(rows[:10]):
        try:
            return index, _resolve_column_indexes(row)
        except ValueError:
            continue
    return None, None


def _infer_column_indexes_from_row(row: list[str]) -> dict[str, int]:
    indexes: dict[str, int] = {"ip": 0}

    status_index = next(
        (
            index
            for index, value in enumerate(row[1:], start=1)
            if _is_status_value(value)
        ),
        None,
    )
    if status_index is None:
        raise ValueError(
            "Unable to infer SNDS status column from headerless row: "
            + " | ".join(cell.strip() for cell in row[:12])
        )
    indexes["overall_status"] = status_index

    numeric_before_status = [
        index for index in range(1, status_index) if _parse_int(row[index]) is not None
    ]
    if numeric_before_status:
        indexes["email_volume"] = numeric_before_status[-1]
    else:
        raise ValueError(
            "Unable to infer SNDS data volume column from headerless row: "
            + " | ".join(cell.strip() for cell in row[:12])
        )

    if len(numeric_before_status) >= 2:
        indexes["message_recipients"] = numeric_before_status[-2]
    if len(numeric_before_status) >= 3:
        indexes["rcpt_commands"] = numeric_before_status[-3]
    elif len(numeric_before_status) >= 2:
        indexes["rcpt_commands"] = numeric_before_status[0]

    for index in range(status_index + 1, len(row)):
        value = row[index].strip()
        if _looks_like_percentage(value):
            indexes["complaint_rate"] = index
            break

    for index in range(len(row) - 1, status_index, -1):
        if _parse_int(row[index]) is not None:
            indexes["trap_hits"] = index
            break

    for index in range(status_index + 1, len(row)):
        normalized = _normalize_column_name(row[index])
        if normalized:
            if "@" in row[index]:
                indexes["jmrp1_sender"] = index
            elif (
                _parse_int(row[index]) is None
                and not _looks_like_percentage(row[index])
                and not _looks_like_datetime_text(row[index])
            ):
                indexes["comments"] = index

    return indexes


def _update_row_metrics(
    ip: str,
    activity_period: str,
    rcpt_commands: str,
    data: str,
    message_recipients: str,
    overall_status: str,
    complaint_rate: str,
    trap_message_period: str,
    trap_hits: str,
    jmrp1_sender: str,
    comments: str,
) -> None:
    activity_period_timestamp = _parse_timestamp(activity_period)
    if activity_period_timestamp is not None:
        activity_period_timestamp_gauge.labels(ip=ip).set(activity_period_timestamp)
    elif activity_period.strip():
        logger.warning("Invalid activity period for IP %s: %s", ip, activity_period)

    rcpt_commands_value = _parse_int(rcpt_commands)
    if rcpt_commands_value is not None:
        rcpt_commands_gauge.labels(ip=ip).set(rcpt_commands_value)
    elif rcpt_commands:
        logger.warning("Invalid RCPT commands value for IP %s: %s", ip, rcpt_commands)

    data_value = _parse_int(data)
    if data_value is not None:
        email_volume_gauge.labels(ip=ip).set(data_value)
    else:
        logger.warning("Invalid DATA value for IP %s: %s", ip, data)

    message_recipients_value = _parse_int(message_recipients)
    if message_recipients_value is not None:
        message_recipients_gauge.labels(ip=ip).set(message_recipients_value)
    elif message_recipients:
        logger.warning(
            "Invalid message recipients value for IP %s: %s", ip, message_recipients
        )

    trap_hits_value = _parse_int(trap_hits)
    if trap_hits_value is not None:
        trap_hits_gauge.labels(ip=ip).set(trap_hits_value)
    elif trap_hits.strip():
        logger.warning("Invalid trap hits value for IP %s: %s", ip, trap_hits)

    complaint_rate_value = _parse_complaint_rate(complaint_rate)
    if complaint_rate_value is not None:
        complaint_rate_gauge.labels(ip=ip).set(complaint_rate_value)
    elif complaint_rate.strip() and "<" not in complaint_rate:
        logger.warning("Invalid complaint rate for IP %s: %s", ip, complaint_rate)

    trap_message_period_timestamp = _parse_timestamp(trap_message_period)
    if trap_message_period_timestamp is not None:
        trap_message_period_timestamp_gauge.labels(ip=ip).set(
            trap_message_period_timestamp
        )
    elif trap_message_period.strip():
        logger.warning(
            "Invalid trap message period for IP %s: %s", ip, trap_message_period
        )

    status_lower = overall_status.strip().lower()
    if status_lower in status_numeric_mapping:
        overall_status_gauge.labels(ip=ip, status=status_lower).set(
            status_numeric_mapping[status_lower]
        )
        overall_status_info_gauge.labels(ip=ip, status=status_lower).set(1)
    elif overall_status.strip():
        logger.warning("Unknown overall status for IP %s: %s", ip, overall_status)

    if jmrp1_sender.strip():
        jmrp1_sender_present_gauge.labels(ip=ip).set(1)

    if comments.strip():
        comments_present_gauge.labels(ip=ip).set(1)


def _update_gauges_from_json(json_content: str) -> int:
    payload = json.loads(json_content)
    if isinstance(payload, dict):
        for key in ("value", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break

    if not isinstance(payload, list):
        raise ValueError("SNDS JSON response does not contain a list of records.")

    processed_rows = 0
    last_sample_keys: list[str] = []

    for item in payload:
        if not isinstance(item, dict):
            continue
        normalized = {
            _normalize_column_name(key): str(value) for key, value in item.items()
        }
        last_sample_keys = list(normalized.keys())
        try:
            indexes = _resolve_column_indexes(list(normalized.keys()))
        except ValueError:
            continue

        values = list(normalized.values())
        ip = values[indexes["ip"]].strip()
        if not _is_ip_address(ip):
            continue

        _update_row_metrics(
            ip,
            values[indexes["activity_period"]] if "activity_period" in indexes else "",
            values[indexes["rcpt_commands"]] if "rcpt_commands" in indexes else "",
            values[indexes["email_volume"]],
            (
                values[indexes["message_recipients"]]
                if "message_recipients" in indexes
                else ""
            ),
            values[indexes["overall_status"]],
            values[indexes["complaint_rate"]] if "complaint_rate" in indexes else "",
            (
                values[indexes["trap_message_period"]]
                if "trap_message_period" in indexes
                else ""
            ),
            values[indexes["trap_hits"]] if "trap_hits" in indexes else "",
            values[indexes["jmrp1_sender"]] if "jmrp1_sender" in indexes else "",
            values[indexes["comments"]] if "comments" in indexes else "",
        )
        processed_rows += 1

    if processed_rows == 0:
        raise ValueError(
            "SNDS JSON format is unsupported. Sample keys: "
            + ", ".join(last_sample_keys[:10])
        )

    return processed_rows


def _update_gauges_from_csv(csv_content: str) -> int:
    rows = [
        row for row in _csv_reader(csv_content) if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise ValueError("SNDS response did not contain any CSV rows.")

    header_index, column_indexes = _find_header_row(rows)
    if column_indexes is not None and header_index is not None:
        data_rows = rows[header_index + 1 :]
    elif _is_ip_address(rows[0][0]):
        column_indexes = _infer_column_indexes_from_row(rows[0])
        data_rows = rows
    else:
        sample_row = " | ".join(cell.strip() for cell in rows[0][:12])
        raise ValueError(f"SNDS CSV format is unsupported. First row: {sample_row}")

    processed_rows = 0
    for row in data_rows:
        if len(row) <= max(column_indexes.values()):
            logger.debug("Skipping incomplete row: %s", row)
            continue

        ip = row[column_indexes["ip"]].strip()
        if not _is_ip_address(ip):
            logger.debug("Skipping non-IP row: %s", row)
            continue

        _update_row_metrics(
            ip,
            (
                row[column_indexes["activity_period"]].strip()
                if "activity_period" in column_indexes
                else ""
            ),
            (
                row[column_indexes["rcpt_commands"]].strip()
                if "rcpt_commands" in column_indexes
                else ""
            ),
            row[column_indexes["email_volume"]].strip(),
            (
                row[column_indexes["message_recipients"]].strip()
                if "message_recipients" in column_indexes
                else ""
            ),
            row[column_indexes["overall_status"]].strip(),
            (
                row[column_indexes["complaint_rate"]].strip()
                if "complaint_rate" in column_indexes
                else ""
            ),
            (
                row[column_indexes["trap_message_period"]].strip()
                if "trap_message_period" in column_indexes
                else ""
            ),
            (
                row[column_indexes["trap_hits"]].strip()
                if "trap_hits" in column_indexes
                else ""
            ),
            (
                row[column_indexes["jmrp1_sender"]].strip()
                if "jmrp1_sender" in column_indexes
                else ""
            ),
            (
                row[column_indexes["comments"]].strip()
                if "comments" in column_indexes
                else ""
            ),
        )
        processed_rows += 1

    if processed_rows == 0:
        raise ValueError("SNDS CSV did not contain any usable data rows.")

    return processed_rows


def _update_gauges(csv_content: str) -> int:
    activity_period_timestamp_gauge.clear()
    rcpt_commands_gauge.clear()
    email_volume_gauge.clear()
    message_recipients_gauge.clear()
    trap_message_period_timestamp_gauge.clear()
    trap_hits_gauge.clear()
    complaint_rate_gauge.clear()
    overall_status_gauge.clear()
    overall_status_info_gauge.clear()
    jmrp1_sender_present_gauge.clear()
    comments_present_gauge.clear()

    stripped = csv_content.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _update_gauges_from_json(csv_content)
    return _update_gauges_from_csv(csv_content)


def fetch_snds_data(force: bool = False) -> None:
    """Fetch SNDS data and update metrics if the cache is stale."""
    global _last_fetch_epoch, _last_fetch_success

    if not API_KEY and not AUTOMATED_DATA_ACCESS_URL and "key=" not in API_URL:
        logger.error(
            "No SNDS automated data access link is configured. Set AUTOMATED_DATA_ACCESS_URL or API_KEY."
        )
        fetch_success_gauge.set(0)
        return

    now = time.time()
    # Fast path without lock if cache is still valid
    if not force and _last_fetch_success and now - _last_fetch_epoch < CACHE_SECONDS:
        return

    with _lock:
        now = time.time()
        if (
            not force
            and _last_fetch_success
            and now - _last_fetch_epoch < CACHE_SECONDS
        ):
            return

        fetch_start = time.time()
        try:
            data_url, request_params = _extract_data_url_and_params()
            response = _session.get(
                data_url,
                params=request_params,
                timeout=REQUEST_TIMEOUT,
                verify=VERIFY_TLS,
            )
            response.raise_for_status()
            _validate_response(response)
            processed_rows = _update_gauges(response.text)
            fetch_parse_error_gauge.set(0)
        except requests.RequestException as exc:
            logger.exception("Failed to fetch SNDS data: %s", exc)
            fetch_success_gauge.set(0)
            fetch_parse_error_gauge.set(0)
            _last_fetch_success = False
            _last_fetch_epoch = time.time()
            return
        except ValueError as exc:
            logger.exception("Failed to fetch SNDS data: %s", exc)
            fetch_success_gauge.set(0)
            fetch_parse_error_gauge.set(1)
            _log_unknown_response_sample(response.text, exc)
            _last_fetch_success = False
            _last_fetch_epoch = time.time()
            return

        fetch_duration = time.time() - fetch_start

        logger.info("Fetched SNDS data for %s IP rows.", processed_rows)
        _last_fetch_epoch = time.time()
        _last_fetch_success = True
        fetch_success_gauge.set(1)
        fetch_timestamp_gauge.set(_last_fetch_epoch)
        fetch_duration_gauge.set(fetch_duration)


@app.route("/healthz")
def healthz():
    if not _last_fetch_success:
        return "SNDS data not yet available", 503
    return "OK", 200


@app.route("/livez")
def livez():
    return "OK", 200


@app.route("/metrics")
def metrics():
    fetch_snds_data()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    serve(app, host=LISTEN_HOST, port=LISTEN_PORT)
