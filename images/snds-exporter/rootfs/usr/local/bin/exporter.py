import csv
import datetime as dt
import ipaddress
import json
import logging
import os
import re
import base64
import stat
import threading
import time
from io import StringIO
from tempfile import NamedTemporaryFile
from typing import Optional
from urllib.parse import quote

import requests
from flask import Flask, Response, request
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
overall_status_info_gauge = Gauge(
    "snds_overall_status_info", "One-hot overall status per IP", ["ip", "status"]
)
ip_status_blocked_gauge = Gauge(
    "snds_ip_status_blocked",
    "1 if the IP range from /api/report/status/ip is blocked, else 0",
    ["range_start", "range_end"],
)
ip_status_reason_info_gauge = Gauge(
    "snds_ip_status_reason_info",
    "One-hot IP status reason per IP range from /api/report/status/ip",
    ["range_start", "range_end", "blocked", "reason"],
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
AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
TOKEN_ENDPOINT = f"{AUTHORITY}/token"
CLIENT_ID = "a53a6cc1-a1cd-46f7-a4aa-281cdabec33c"
SNDS_SCOPE = "a53a6cc1-a1cd-46f7-a4aa-281cdabec33c/.default"
REST_API_URL = os.getenv(
    "REST_API_URL",
    "https://substrate.office.com/ip-domain-management-snds/api/report/data",
)
STATUS_API_URL = os.getenv(
    "STATUS_API_URL",
    "https://substrate.office.com/ip-domain-management-snds/api/report/status/ip",
)
REST_API_DATE = os.getenv("REST_API_DATE", "").strip()
REST_API_IP = os.getenv("REST_API_IP", "").strip()
REST_API_LOOKBACK_DAYS = max(1, int(os.getenv("REST_API_LOOKBACK_DAYS", "3")))
SNDS_ACCESS_TOKEN = os.getenv("SNDS_ACCESS_TOKEN", "")
SNDS_ACCESS_TOKEN_FILE = os.getenv("SNDS_ACCESS_TOKEN_FILE", "")
SNDS_TOKEN_CACHE_FILE = os.getenv("SNDS_TOKEN_CACHE_FILE", "")
TOKEN_REFRESH_BEFORE_SECONDS = max(
    30, int(os.getenv("TOKEN_REFRESH_BEFORE_SECONDS", "600"))
)
K8S_SECRET_NAME = os.getenv("K8S_SECRET_NAME", "").strip()
K8S_SECRET_NAMESPACE = os.getenv("K8S_SECRET_NAMESPACE", "").strip()
K8S_SECRET_ACCESS_TOKEN_KEY = os.getenv(
    "K8S_SECRET_ACCESS_TOKEN_KEY", "access-token"
).strip()
K8S_SECRET_CACHE_KEY = os.getenv("K8S_SECRET_CACHE_KEY", "token-cache.json").strip()
K8S_API_URL = os.getenv("K8S_API_URL", "https://kubernetes.default.svc").rstrip("/")
K8S_SERVICE_ACCOUNT_TOKEN_FILE = os.getenv(
    "K8S_SERVICE_ACCOUNT_TOKEN_FILE",
    "/var/run/secrets/kubernetes.io/serviceaccount/token",
)
K8S_SERVICE_ACCOUNT_CA_FILE = os.getenv(
    "K8S_SERVICE_ACCOUNT_CA_FILE",
    "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
)
K8S_NAMESPACE_FILE = os.getenv(
    "K8S_NAMESPACE_FILE",
    "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
)
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
_auth_lock = threading.Lock()
_last_fetch_epoch = 0.0
_last_fetch_success = False
_access_token_cache = SNDS_ACCESS_TOKEN.strip()
_access_token_expires_at: Optional[int] = None
_refresh_token_cache = ""
_auth_state_loaded = False


def _default_token_file_path() -> str:
    xdg_state_home = os.getenv("XDG_STATE_HOME")
    if xdg_state_home:
        return os.path.join(xdg_state_home, "snds-exporter", "access-token")
    return os.path.join(
        os.path.expanduser("~"), ".local", "state", "snds-exporter", "access-token"
    )


def _default_token_cache_file_path() -> str:
    xdg_cache_home = os.getenv("XDG_CACHE_HOME")
    if xdg_cache_home:
        return os.path.join(xdg_cache_home, "snds-exporter", "token-cache.json")
    return os.path.join(
        os.path.expanduser("~"), ".cache", "snds-exporter", "token-cache.json"
    )


def _write_secure_text(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=directory or ".",
        delete=False,
    ) as temp_file:
        temp_file.write(content)
        temp_path = temp_file.name
    os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temp_path, path)


def _path_is_writable(path: str) -> bool:
    if os.path.exists(path):
        return os.access(path, os.W_OK)
    parent = os.path.dirname(path) or "."
    return os.access(parent, os.W_OK)


def _token_cache_payload(expires_at: int, refresh_token: str) -> dict:
    return {
        "access_token_expires_at": expires_at,
        "refresh_token": refresh_token,
    }


def _load_token_cache() -> dict:
    cache_file_path = SNDS_TOKEN_CACHE_FILE or _default_token_cache_file_path()
    if not cache_file_path or not os.path.exists(cache_file_path):
        return {}
    with open(cache_file_path, encoding="utf-8") as cache_file:
        raw_content = cache_file.read().strip()
    if not raw_content:
        return {}
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        logger.warning(
            "Ignoring invalid SNDS token cache JSON in %s until valid token state is written.",
            cache_file_path,
        )
        return {}


def _token_expiry_epoch(result: dict) -> int:
    expires_on = result.get("expires_on")
    if expires_on:
        return int(expires_on)
    expires_in = result.get("expires_in")
    if expires_in:
        return int(time.time()) + int(expires_in)
    raise RuntimeError("Microsoft token response did not include an expiry time.")


def _token_error(result: dict | None) -> str:
    if not result:
        return "No token result returned."
    error = result.get("error")
    description = result.get("error_description")
    if error or description:
        return f"{error or 'token_error'}: {description or 'no description'}"
    return "Unknown token acquisition failure."


def _load_auth_state(force: bool = False) -> None:
    global _access_token_cache, _access_token_expires_at, _refresh_token_cache, _auth_state_loaded
    if _auth_state_loaded and not force:
        return

    _access_token_cache = SNDS_ACCESS_TOKEN.strip()
    _access_token_expires_at = None
    _refresh_token_cache = ""
    token_file_path = SNDS_ACCESS_TOKEN_FILE or _default_token_file_path()
    if token_file_path and os.path.exists(token_file_path):
        with open(token_file_path, encoding="utf-8") as token_file:
            _access_token_cache = token_file.read().strip()

    cache_payload = _load_token_cache()
    expires_at = cache_payload.get("access_token_expires_at")
    _access_token_expires_at = int(expires_at) if expires_at else None
    _refresh_token_cache = cache_payload.get("refresh_token", "").strip()
    _auth_state_loaded = True


def _store_auth_state(
    access_token: str,
    expires_at: int,
    refresh_token: str,
) -> None:
    global _access_token_cache, _access_token_expires_at, _refresh_token_cache, _auth_state_loaded
    _access_token_cache = access_token.strip()
    _access_token_expires_at = expires_at
    _refresh_token_cache = refresh_token.strip()
    _auth_state_loaded = True


def _service_account_namespace() -> str:
    if K8S_SECRET_NAMESPACE:
        return K8S_SECRET_NAMESPACE
    if os.path.exists(K8S_NAMESPACE_FILE):
        with open(K8S_NAMESPACE_FILE, encoding="utf-8") as namespace_file:
            return namespace_file.read().strip()
    return ""


def _persist_auth_state(
    access_token: str,
    expires_at: int,
    refresh_token: str,
) -> None:
    token_file_path = SNDS_ACCESS_TOKEN_FILE or _default_token_file_path()
    cache_file_path = SNDS_TOKEN_CACHE_FILE or _default_token_cache_file_path()
    cache_payload = _token_cache_payload(expires_at, refresh_token)

    if token_file_path and _path_is_writable(token_file_path):
        _write_secure_text(token_file_path, access_token + "\n")
    if cache_file_path and _path_is_writable(cache_file_path):
        _write_secure_text(
            cache_file_path,
            json.dumps(cache_payload, indent=2, sort_keys=True) + "\n",
        )

    if not K8S_SECRET_NAME:
        return

    namespace = _service_account_namespace()
    if not namespace:
        logger.warning(
            "SNDS token refresh succeeded, but Kubernetes secret update is disabled because no namespace was found."
        )
        return
    if not os.path.exists(K8S_SERVICE_ACCOUNT_TOKEN_FILE):
        logger.warning(
            "SNDS token refresh succeeded, but Kubernetes secret update is disabled because the service account token file is missing."
        )
        return

    with open(K8S_SERVICE_ACCOUNT_TOKEN_FILE, encoding="utf-8") as token_file:
        service_account_token = token_file.read().strip()

    patch_payload = {
        "data": {
            K8S_SECRET_ACCESS_TOKEN_KEY: base64.b64encode(
                (access_token + "\n").encode("utf-8")
            ).decode("ascii"),
            K8S_SECRET_CACHE_KEY: base64.b64encode(
                (json.dumps(cache_payload, indent=2, sort_keys=True) + "\n").encode(
                    "utf-8"
                )
            ).decode("ascii"),
        }
    }
    verify_value: bool | str = (
        K8S_SERVICE_ACCOUNT_CA_FILE if os.path.exists(K8S_SERVICE_ACCOUNT_CA_FILE) else True
    )
    try:
        response = requests.patch(
            f"{K8S_API_URL}/api/v1/namespaces/{namespace}/secrets/{K8S_SECRET_NAME}",
            headers={
                "Authorization": f"Bearer {service_account_token}",
                "Content-Type": "application/merge-patch+json",
            },
            data=json.dumps(patch_payload),
            timeout=30,
            verify=verify_value,
        )
        response.raise_for_status()
        logger.info(
            "Updated Kubernetes secret %s/%s with refreshed SNDS token state.",
            namespace,
            K8S_SECRET_NAME,
        )
    except Exception as exc:
        logger.warning(
            "SNDS token refresh succeeded, but updating Kubernetes secret %s/%s failed: %s",
            namespace,
            K8S_SECRET_NAME,
            exc,
        )


def _refresh_access_token(force: bool = False) -> str:
    with _auth_lock:
        _load_auth_state()
        now = int(time.time())
        if (
            not force
            and _access_token_cache
            and _access_token_expires_at is not None
            and now < _access_token_expires_at - TOKEN_REFRESH_BEFORE_SECONDS
        ):
            return _access_token_cache
        if not force and _access_token_cache and not _refresh_token_cache:
            return _access_token_cache
        if not _refresh_token_cache:
            raise RuntimeError(
                "No refresh token is available for headless SNDS token renewal. Human intervention is required."
            )

        logger.info("Refreshing SNDS access token using cached refresh token.")
        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": _refresh_token_cache,
                "scope": SNDS_SCOPE,
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        if "access_token" not in result:
            raise RuntimeError(_token_error(result))
        access_token = result["access_token"].strip()
        expires_at = _token_expiry_epoch(result)
        refresh_token = result.get("refresh_token", "").strip() or _refresh_token_cache
        _store_auth_state(access_token, expires_at, refresh_token)
        _persist_auth_state(access_token, expires_at, refresh_token)
        return access_token


def _normalize_column_name(value: str) -> str:
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
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


def _load_access_token() -> str:
    _load_auth_state()
    return _access_token_cache


def _has_auth_material() -> bool:
    _load_auth_state()
    if not (_access_token_cache or _refresh_token_cache):
        _load_auth_state(force=True)
    return bool(_access_token_cache or _refresh_token_cache)


def _default_rest_api_date() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )


def _default_rest_api_dates() -> list[str]:
    start = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    return [
        (start - dt.timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(REST_API_LOOKBACK_DAYS)
    ]


def _build_request(
    rest_api_date: Optional[str] = None,
    rest_api_ip: Optional[str] = None,
) -> tuple[str, dict[str, str], dict[str, str]]:
    access_token = _refresh_access_token()

    selected_date = (REST_API_DATE if rest_api_date is None else rest_api_date).strip()
    selected_ip = (REST_API_IP if rest_api_ip is None else rest_api_ip).strip()
    if not selected_date:
        selected_date = _default_rest_api_date()
    rest_api_url = REST_API_URL.rstrip("/")
    if selected_date:
        rest_api_url = f"{rest_api_url}/{quote(selected_date, safe='')}"
    if selected_ip:
        rest_api_url = f"{rest_api_url}/{quote(selected_ip, safe='')}"
    return (
        rest_api_url,
        {},
        {"Authorization": f"Bearer {access_token}"},
    )


def _build_rest_request_candidates(
    rest_api_date: Optional[str] = None,
    rest_api_ip: Optional[str] = None,
) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    access_token = _refresh_access_token()

    selected_date = (REST_API_DATE if rest_api_date is None else rest_api_date).strip()
    selected_ip = (REST_API_IP if rest_api_ip is None else rest_api_ip).strip()
    dates = [selected_date] if selected_date else _default_rest_api_dates()
    candidates = []
    for candidate_date in dates:
        rest_api_url = REST_API_URL.rstrip("/")
        if candidate_date:
            rest_api_url = f"{rest_api_url}/{quote(candidate_date, safe='')}"
        if selected_ip:
            rest_api_url = f"{rest_api_url}/{quote(selected_ip, safe='')}"
        candidates.append(
            (rest_api_url, {}, {"Authorization": f"Bearer {access_token}"})
        )
    return candidates


def _build_status_request() -> tuple[str, dict[str, str], dict[str, str]]:
    access_token = _refresh_access_token()
    return STATUS_API_URL.rstrip("/"), {}, {"Authorization": f"Bearer {access_token}"}


def _is_rest_api_request(url: str, headers: dict[str, str]) -> bool:
    return bool(headers.get("Authorization")) and "/api/report/" in url


def _request_with_rest_fallback(
    data_url: str,
    request_params: dict[str, str],
    request_headers: dict[str, str],
) -> requests.Response:
    response = _session.get(
        data_url,
        params=request_params,
        headers=request_headers,
        timeout=REQUEST_TIMEOUT,
        verify=VERIFY_TLS,
    )
    if (
        response.status_code == 404
        and _is_rest_api_request(data_url, request_headers)
        and not data_url.endswith("/")
    ):
        retry_url = f"{data_url}/"
        retry_response = _session.get(
            retry_url,
            params=request_params,
            headers=request_headers,
            timeout=REQUEST_TIMEOUT,
            verify=VERIFY_TLS,
        )
        if retry_response.ok:
            logger.info("SNDS REST API succeeded after retrying with trailing slash.")
            return retry_response
        response = retry_response
    return response


def _request_with_auth_refresh(
    data_url: str,
    request_params: dict[str, str],
    request_headers: dict[str, str],
) -> requests.Response:
    response = _request_with_rest_fallback(
        data_url,
        request_params,
        request_headers,
    )
    if response.status_code == 401 and request_headers.get("Authorization"):
        logger.warning("SNDS access token was rejected; attempting an immediate refresh.")
        refreshed_headers = dict(request_headers)
        refreshed_headers["Authorization"] = f"Bearer {_refresh_access_token(force=True)}"
        response = _request_with_rest_fallback(
            data_url,
            request_params,
            refreshed_headers,
        )
    return response


def _http_error_details(response: requests.Response) -> str:
    body = " ".join(response.text.split())
    if body:
        return f"HTTP {response.status_code} response body: {body[:300]}"
    return f"HTTP {response.status_code} response body was empty."


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


def _resolve_status_column_indexes(header: list[str]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    normalized = [_normalize_column_name(column) for column in header]

    status_aliases = {
        "range_start": {
            "beginning ip address",
            "starting ip address",
            "start ip",
            "ip address",
            "ip",
        },
        "range_end": {"ending ip address", "ending ip", "end ip"},
        "blocked": {"blocked", "is blocked", "listed", "is listed"},
        "reason": {"reason", "description", "details", "comment", "comments"},
    }

    for logical_name in ("range_start", "range_end", "blocked"):
        aliases = status_aliases[logical_name]
        for index, column_name in enumerate(normalized):
            if column_name in aliases:
                indexes[logical_name] = index
                break

    for index, column_name in enumerate(normalized):
        if "reason" in indexes:
            break
        if column_name in status_aliases["reason"]:
            indexes["reason"] = index

    missing = {"range_start", "range_end", "blocked"} - set(indexes)
    if missing:
        raise ValueError(
            "SNDS status format is unsupported. Missing columns: "
            + ", ".join(sorted(missing))
        )

    return indexes


def _parse_bool(value: str) -> Optional[bool]:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _infer_status_from_blocked_flag(value: str) -> Optional[str]:
    blocked = _parse_bool(value)
    if blocked is None:
        return None
    return "red" if blocked else "green"


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


def _update_ip_status_metrics(
    range_start: str,
    range_end: str,
    blocked_value: str,
    reason: str,
) -> None:
    blocked = _parse_bool(blocked_value)
    if blocked is None:
        logger.warning(
            "Unknown blocked flag for IP range %s-%s: %s",
            range_start,
            range_end,
            blocked_value,
        )
        return

    blocked_label = "true" if blocked else "false"
    ip_status_blocked_gauge.labels(
        range_start=range_start,
        range_end=range_end,
    ).set(1 if blocked else 0)
    if reason.strip():
        ip_status_reason_info_gauge.labels(
            range_start=range_start,
            range_end=range_end,
            blocked=blocked_label,
            reason=reason.strip(),
        ).set(1)


def _update_ip_status_gauges_from_json(json_content: str) -> int:
    payload = json.loads(json_content)
    if isinstance(payload, dict):
        for key in ("value", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break

    if not isinstance(payload, list):
        raise ValueError(
            "SNDS status JSON response does not contain a list of records."
        )

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
            indexes = _resolve_status_column_indexes(list(normalized.keys()))
        except ValueError:
            continue

        values = list(normalized.values())
        range_start = values[indexes["range_start"]].strip()
        range_end = values[indexes["range_end"]].strip()
        if not _is_ip_address(range_start) or not _is_ip_address(range_end):
            continue

        _update_ip_status_metrics(
            range_start,
            range_end,
            values[indexes["blocked"]],
            values[indexes["reason"]] if "reason" in indexes else "",
        )
        processed_rows += 1

    if processed_rows == 0:
        raise ValueError(
            "SNDS status JSON format is unsupported. Sample keys: "
            + ", ".join(last_sample_keys[:10])
        )

    return processed_rows


def _update_ip_status_gauges_from_csv(csv_content: str) -> int:
    rows = [
        row for row in _csv_reader(csv_content) if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise ValueError("SNDS status response did not contain any CSV rows.")

    header_index = None
    column_indexes = None
    for index, row in enumerate(rows[:10]):
        try:
            header_index = index
            column_indexes = _resolve_status_column_indexes(row)
            break
        except ValueError:
            continue

    if column_indexes is not None and header_index is not None:
        data_rows = rows[header_index + 1 :]
    else:
        first_row = rows[0]
        if (
            len(first_row) >= 4
            and _is_ip_address(first_row[0].strip())
            and _is_ip_address(first_row[1].strip())
            and _parse_bool(first_row[2]) is not None
        ):
            data_rows = rows
            processed_rows = 0
            for row in data_rows:
                if len(row) < 4:
                    continue
                range_start = row[0].strip()
                range_end = row[1].strip()
                if not _is_ip_address(range_start) or not _is_ip_address(range_end):
                    continue
                if _parse_bool(row[2]) is None:
                    continue
                _update_ip_status_metrics(
                    range_start,
                    range_end,
                    row[2].strip(),
                    row[3].strip(),
                )
                processed_rows += 1

            if processed_rows == 0:
                raise ValueError(
                    "SNDS status CSV did not contain any usable data rows."
                )
            return processed_rows

        sample_row = " | ".join(cell.strip() for cell in rows[0][:12])
        raise ValueError(
            f"SNDS status CSV format is unsupported. First row: {sample_row}"
        )

    processed_rows = 0
    for row in data_rows:
        if len(row) <= max(column_indexes.values()):
            continue
        range_start = row[column_indexes["range_start"]].strip()
        range_end = row[column_indexes["range_end"]].strip()
        if not _is_ip_address(range_start) or not _is_ip_address(range_end):
            continue
        _update_ip_status_metrics(
            range_start,
            range_end,
            row[column_indexes["blocked"]].strip(),
            row[column_indexes["reason"]].strip() if "reason" in column_indexes else "",
        )
        processed_rows += 1

    if processed_rows == 0:
        raise ValueError("SNDS status CSV did not contain any usable data rows.")

    return processed_rows


def _update_ip_status_gauges(content: str) -> int:
    ip_status_blocked_gauge.clear()
    ip_status_reason_info_gauge.clear()

    stripped = content.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _update_ip_status_gauges_from_json(content)
    return _update_ip_status_gauges_from_csv(content)


def _update_gauges(csv_content: str) -> int:
    activity_period_timestamp_gauge.clear()
    rcpt_commands_gauge.clear()
    email_volume_gauge.clear()
    message_recipients_gauge.clear()
    trap_message_period_timestamp_gauge.clear()
    trap_hits_gauge.clear()
    complaint_rate_gauge.clear()
    overall_status_info_gauge.clear()
    jmrp1_sender_present_gauge.clear()
    comments_present_gauge.clear()

    stripped = csv_content.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return _update_gauges_from_json(csv_content)
    return _update_gauges_from_csv(csv_content)


def fetch_snds_data(
    force: bool = False,
    rest_api_date: Optional[str] = None,
    rest_api_ip: Optional[str] = None,
) -> None:
    """Fetch SNDS data and update metrics if the cache is stale."""
    global _last_fetch_epoch, _last_fetch_success
    manual_rest_override = rest_api_date is not None or rest_api_ip is not None

    if not _has_auth_material():
        logger.error(
            "No SNDS REST API token state is configured. Provide an access token and refresh-token cache from the initial login."
        )
        fetch_success_gauge.set(0)
        return

    now = time.time()
    # Fast path without lock if cache is still valid
    if (
        not force
        and not manual_rest_override
        and _last_fetch_success
        and now - _last_fetch_epoch < CACHE_SECONDS
    ):
        return

    with _lock:
        now = time.time()
        if (
            not force
            and not manual_rest_override
            and _last_fetch_success
            and now - _last_fetch_epoch < CACHE_SECONDS
        ):
            return

        fetch_start = time.time()
        try:
            request_candidates = _build_rest_request_candidates(
                rest_api_date=rest_api_date,
                rest_api_ip=rest_api_ip,
            )
            response = None
            last_exc = None
            for data_url, request_params, request_headers in request_candidates:
                response = _request_with_auth_refresh(
                    data_url,
                    request_params,
                    request_headers,
                )
                try:
                    response.raise_for_status()
                    if len(request_candidates) > 1:
                        logger.info(
                            "Using SNDS REST report date from %s",
                            data_url.rsplit("/", 1)[-1],
                        )
                    break
                except requests.RequestException as exc:
                    last_exc = exc
                    if (
                        response.status_code == 404
                        and _is_rest_api_request(data_url, request_headers)
                        and len(request_candidates) > 1
                    ):
                        continue
                    raise
            else:
                if last_exc is not None:
                    raise last_exc

            _validate_response(response)
            processed_rows = _update_gauges(response.text)
            processed_status_rows = 0
            status_url, status_params, status_headers = _build_status_request()
            status_response = _request_with_auth_refresh(
                status_url,
                status_params,
                status_headers,
            )
            status_response.raise_for_status()
            _validate_response(status_response)
            processed_status_rows = _update_ip_status_gauges(status_response.text)
            fetch_parse_error_gauge.set(0)
        except requests.RequestException as exc:
            if (
                "response" in locals()
                and getattr(response, "status_code", None) is not None
            ):
                logger.error(_http_error_details(response))
            if (
                "status_response" in locals()
                and getattr(status_response, "status_code", None) is not None
            ):
                logger.error(_http_error_details(status_response))
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

        logger.info(
            "Fetched SNDS data for %s IP rows and SNDS IP status for %s IP rows.",
            processed_rows,
            processed_status_rows,
        )
        _last_fetch_epoch = time.time()
        _last_fetch_success = True
        fetch_success_gauge.set(1)
        fetch_timestamp_gauge.set(_last_fetch_epoch)
        fetch_duration_gauge.set(fetch_duration)


@app.route("/healthz")
def healthz():
    if not _has_auth_material():
        return "OK", 200
    if _last_fetch_epoch == 0:
        return "OK", 200
    if not _last_fetch_success:
        return "SNDS data not yet available", 503
    return "OK", 200


@app.route("/livez")
def livez():
    return "OK", 200


@app.route("/metrics")
def metrics():
    rest_api_date = request.args.get("date")
    rest_api_ip = request.args.get("ip")
    fetch_snds_data(
        force=rest_api_date is not None or rest_api_ip is not None,
        rest_api_date=rest_api_date,
        rest_api_ip=rest_api_ip,
    )
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    serve(app, host=LISTEN_HOST, port=LISTEN_PORT)
