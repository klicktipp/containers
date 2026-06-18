# snds-exporter

`snds-exporter` is a small Prometheus exporter for Microsoft Smart Network Data Services (SNDS).

It fetches report data from the SNDS REST API, renews the OAuth access token headlessly when a cached refresh token is available, converts the response into Prometheus gauges, and exposes them on port `9100`.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Configuration

### Required Configuration

- `SNDS_ACCESS_TOKEN`: OAuth bearer token for the SNDS REST API.
- `SNDS_ACCESS_TOKEN_FILE`: Path to a file containing the OAuth bearer token for the SNDS REST API.
- `SNDS_TOKEN_CACHE_FILE`: Path to a JSON file containing the cached refresh token and access token expiry. If unset, the exporter uses `~/.cache/snds-exporter/token-cache.json`.

If `SNDS_ACCESS_TOKEN_FILE` is unset, the exporter automatically looks for the helper's default token file at `~/.local/state/snds-exporter/access-token`.
For headless renewal, the exporter needs both the current access token and the refresh-token cache created by the initial login.

### Optional Configuration

- `REST_API_URL`: SNDS REST data endpoint. Default: `https://substrate.office.com/ip-domain-management-snds/api/report/data`
- `STATUS_API_URL`: SNDS REST IP status endpoint. Default: `https://substrate.office.com/ip-domain-management-snds/api/report/status/ip`
- `REST_API_DATE`: Optional SNDS REST report date in `YYYY-MM-DD` format. Appended as `/REST_API_DATE`. If unset, the exporter starts with yesterday's date in UTC and automatically looks back a few days if Microsoft has not published that report yet.
- `REST_API_IP`: Optional SNDS REST IPv4 filter. Appended after `REST_API_DATE` as `/REST_API_IP`.
- `REST_API_LOOKBACK_DAYS`: Number of UTC dates to try when `REST_API_DATE` is unset. Default: `3`
- `TOKEN_REFRESH_BEFORE_SECONDS`: Renew the SNDS access token this many seconds before its cached expiry. Default: `600`
- `K8S_SECRET_NAME`: Optional Kubernetes secret name to patch after a successful token refresh.
- `K8S_SECRET_NAMESPACE`: Optional Kubernetes namespace for `K8S_SECRET_NAME`. If unset, the exporter uses the in-cluster service-account namespace file.
- `K8S_SECRET_ACCESS_TOKEN_KEY`: Secret key for the access token. Default: `access-token`
- `K8S_SECRET_CACHE_KEY`: Secret key for the refresh-token cache JSON. Default: `token-cache.json`
- `K8S_API_URL`: Kubernetes API base URL. Default: `https://kubernetes.default.svc`
- `REQUEST_TIMEOUT`: HTTP timeout in seconds. Default: `10`
- `REQUEST_RETRY_ATTEMPTS`: Number of attempts for transient SNDS transport errors such as connection resets. Default: `3`
- `REQUEST_RETRY_BACKOFF_SECONDS`: Linear backoff base in seconds between retry attempts. Default: `1`
- `CACHE_SECONDS`: Cache duration before the next upstream fetch. Default: `300`
- `VERIFY_TLS`: Enable or disable TLS verification. Default: `true`
- `USER_AGENT`: HTTP user agent. Default: `kt-snds-exporter/1.0`
- `DEBUG_UNKNOWN_RESPONSES`: Log a small debug sample when parsing fails. Default: `false`

The exporter does not perform the interactive OAuth authorization code flow itself. Do the initial login once, provide the resulting access token and refresh-token cache to the pod, and then let the exporter handle silent refresh attempts on demand.

For manual REST endpoint checks without changing environment variables, call `/metrics` with query parameters such as `?date=2026-12-31` or `?date=2026-12-31&ip=192.0.2.4`. Query parameters override `REST_API_DATE` and `REST_API_IP` for that request and bypass the cache.

When REST authentication is used, the exporter fetches both the dated data report and the separate IP status report. The data report populates `snds_overall_status_info`; the IP status report populates range-based metrics such as `snds_ip_status_blocked` and `snds_ip_status_reason_info`.

## Initial Login

Microsoft's legacy SNDS links can expire. The recommended setup for the exporter is therefore the REST API plus an initial OAuth login that seeds the access token and refresh-token cache.

The repository includes a helper script at `rootfs/usr/local/bin/snds_token_helper.py`. The built image contains it at `/usr/local/bin/snds_token_helper.py`. It performs the SNDS OAuth flow directly, requests the `offline_access` scope during the initial login, stores the returned refresh token in a local cache file, and writes the current bearer token to a local file.

Install the helper dependencies on the host:

```sh
python3 -m pip install -r requirements.txt
```

Run the first interactive login once:

```sh
python3 rootfs/usr/local/bin/snds_token_helper.py
```

The helper prints a Microsoft login URL. Open it in your local browser, complete the login, then copy the final redirect URL from the browser address bar and paste it back into the terminal. The browser may show a connection error on `http://localhost`; that is expected for this manual flow.

Inside the image, the equivalent commands are:

```sh
python3 /usr/local/bin/snds_token_helper.py
```

By default, the helper stores:

- the current access token in `~/.local/state/snds-exporter/access-token`
- the refresh token cache in `~/.cache/snds-exporter/token-cache.json`

Provide both files to the exporter container:

```sh
docker run --rm \
  -p 9100:9100 \
  -e SNDS_ACCESS_TOKEN_FILE=/run/secrets/snds-access-token \
  -e SNDS_TOKEN_CACHE_FILE=/run/secrets/snds-token-cache.json \
  -v "$HOME/.local/state/snds-exporter/access-token:/run/secrets/snds-access-token:ro" \
  -v "$HOME/.cache/snds-exporter/token-cache.json:/run/secrets/snds-token-cache.json:ro" \
  local/snds-exporter:latest
```

If silent refresh stops working, the exporter logs the failure to stdout and keeps requiring human intervention for a fresh initial login.

In Kubernetes, mount both files from a Secret. If you want refreshed token state to survive pod restarts and be shared across replicas, set `K8S_SECRET_NAME` and let the exporter patch that Secret after a successful refresh.

The Helm chart in this repository bootstraps that Secret as empty by default so the pod can start before the first login is completed. In that state, `/metrics` will log authentication errors until valid token state exists.

### Kubernetes Bootstrap

The most reliable first-run flow in Kubernetes is:

1. Deploy the chart so the pod starts with the empty bootstrap Secret.
2. Run the interactive login inside the running pod:

```sh
kubectl exec -it deploy/snds-exporter -- python3 /usr/local/bin/snds_token_helper.py
```

3. Complete the Microsoft login in your local browser and paste the final redirect URL back into the shell.
4. Restart the workload once so the exporter definitely reopens the updated Secret contents:

```sh
kubectl rollout restart deployment/snds-exporter
```

If your release name or namespace differs, adjust the resource name accordingly, for example:

```sh
kubectl -n monitoring exec -it deploy/my-snds-exporter -- python3 /usr/local/bin/snds_token_helper.py
kubectl -n monitoring rollout restart deployment/my-snds-exporter
```

The restart is documented here as the reliable path. Kubernetes updates mounted Secret volumes asynchronously, so immediate reuse without a restart is not guaranteed at the exact moment the initial login completes.

## Exposed Endpoints

- `/metrics`: Prometheus metrics endpoint
- `/healthz`: Returns `200` while the process is healthy, including bootstrap before the first successful SNDS fetch
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
