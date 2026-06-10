# snds-exporter

`snds-exporter` is a small Prometheus exporter for Microsoft Smart Network Data Services (SNDS).

It fetches report data from the SNDS REST API or the legacy automated data access endpoint, converts the response into Prometheus gauges, and exposes them on port `9100`.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Configuration

### Required Configuration

Preferred:

- `SNDS_ACCESS_TOKEN`: OAuth bearer token for the SNDS REST API.
- `SNDS_ACCESS_TOKEN_FILE`: Path to a file containing the OAuth bearer token for the SNDS REST API.

If `SNDS_ACCESS_TOKEN_FILE` is unset, the exporter automatically looks for the helper's default token file at `~/.local/state/snds-exporter/access-token`.

Legacy fallback:

- `AUTOMATED_DATA_ACCESS_URL`: Full SNDS automated data access URL.
- `API_KEY`: SNDS key used for the `Key` query parameter when `AUTOMATED_DATA_ACCESS_URL` is not provided.

### Optional Configuration

- `REST_API_URL`: SNDS REST data endpoint. Default: `https://substrate.office.com/ip-domain-management-snds/api/report/data`
- `STATUS_API_URL`: SNDS REST IP status endpoint. Default: `https://substrate.office.com/ip-domain-management-snds/api/report/status/ip`
- `REST_API_DATE`: Optional SNDS REST report date in `YYYY-MM-DD` format. Appended as `/REST_API_DATE`. If unset, the exporter starts with yesterday's date in UTC and automatically looks back a few days if Microsoft has not published that report yet.
- `REST_API_IP`: Optional SNDS REST IPv4 filter. Appended after `REST_API_DATE` as `/REST_API_IP`.
- `REST_API_LOOKBACK_DAYS`: Number of UTC dates to try when `REST_API_DATE` is unset. Default: `3`
- `API_URL`: SNDS data endpoint base URL. Default: `https://substrate.office.com/ip-domain-management-snds/SNDS/DataKey`
- `REQUEST_TIMEOUT`: HTTP timeout in seconds. Default: `10`
- `CACHE_SECONDS`: Cache duration before the next upstream fetch. Default: `300`
- `VERIFY_TLS`: Enable or disable TLS verification. Default: `true`
- `USER_AGENT`: HTTP user agent. Default: `kt-snds-exporter/1.0`
- `DEBUG_UNKNOWN_RESPONSES`: Log a small debug sample when parsing fails. Default: `false`

The exporter does not perform the interactive OAuth authorization code flow itself. Obtain and refresh the SNDS bearer token outside the container, then inject it through `SNDS_ACCESS_TOKEN` or `SNDS_ACCESS_TOKEN_FILE`.

For manual REST endpoint checks without changing environment variables, call `/metrics` with query parameters such as `?date=2026-12-31` or `?date=2026-12-31&ip=192.0.2.4`. Query parameters override `REST_API_DATE` and `REST_API_IP` for that request and bypass the cache.

When REST authentication is used, the exporter fetches both the dated data report and the separate IP status report. The data report populates `snds_overall_status_info`; the IP status report populates range-based metrics such as `snds_ip_status_blocked` and `snds_ip_status_reason_info`.

## Token Helper

Microsoft's legacy SNDS links can expire. The recommended setup for the exporter is therefore the REST API plus a host-side token refresher.

The repository includes a helper script at `rootfs/usr/local/bin/snds_token_helper.py`. The built image contains it at `/usr/local/bin/snds_token_helper.py`. It performs the SNDS OAuth flow directly, stores a refresh token cache on the host, writes the current bearer token to a local file, and can keep refreshing it before expiry.

Install the helper dependencies on the host:

```sh
python3 -m pip install -r requirements.txt
```

Run the first interactive login once:

```sh
python3 rootfs/usr/local/bin/snds_token_helper.py
```

The helper prints a Microsoft login URL. Open it in your local browser, complete the login, then copy the final redirect URL from the browser address bar and paste it back into the terminal. The browser may show a connection error on `http://localhost`; that is expected for this manual flow.

Keep the token fresh in the background:

```sh
python3 rootfs/usr/local/bin/snds_token_helper.py --watch
```

Inside the image, the equivalent commands are:

```sh
python3 /usr/local/bin/snds_token_helper.py
python3 /usr/local/bin/snds_token_helper.py --watch
```

For unattended refresh jobs, use `--non-interactive` so the helper fails cleanly instead of waiting for pasted browser output:

```sh
python3 /usr/local/bin/snds_token_helper.py --watch --non-interactive
```

By default, the helper stores:

- the current access token in `~/.local/state/snds-exporter/access-token`
- the refresh token cache in `~/.cache/snds-exporter/token-cache.json`

Point the container at the token file:

```sh
docker run --rm \
  -p 9100:9100 \
  -e SNDS_ACCESS_TOKEN_FILE=/run/secrets/snds-access-token \
  -v "$HOME/.local/state/snds-exporter/access-token:/run/secrets/snds-access-token:ro" \
  local/snds-exporter:latest
```

If the cache can no longer refresh silently, the helper will require another interactive Microsoft login. In `--watch --non-interactive` mode, it fails cleanly so your job logs and alerts can detect the problem.

## Exposed Endpoints

- `/metrics`: Prometheus metrics endpoint
- `/healthz`: Returns `200` after at least one successful SNDS fetch
- `/livez`: Basic liveness endpoint

## Local Validation

Run the local checks from the image directory:

```sh
make check
```

Build only:

```sh
docker build -t local/snds-exporter:latest .
```
