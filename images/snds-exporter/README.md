# snds-exporter

`snds-exporter` is a small Prometheus exporter for Microsoft Smart Network Data Services (SNDS).

It fetches report data from the SNDS automated data access endpoint, converts the response into Prometheus gauges, and exposes them on port `9100`.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Configuration

### Required Configuration

Provide one of the following:

- `AUTOMATED_DATA_ACCESS_URL`: Full SNDS automated data access URL.
- `API_KEY`: SNDS key appended to `API_URL` when a full access URL is not provided.

### Optional Configuration

- `API_URL`: SNDS endpoint base URL. Default: `https://sendersupport.olc.protection.outlook.com/snds/data.aspx`
- `REQUEST_TIMEOUT`: HTTP timeout in seconds. Default: `10`
- `CACHE_SECONDS`: Cache duration before the next upstream fetch. Default: `300`
- `VERIFY_TLS`: Enable or disable TLS verification. Default: `true`
- `USER_AGENT`: HTTP user agent. Default: `kt-snds-exporter/1.0`
- `DEBUG_UNKNOWN_RESPONSES`: Log a small debug sample when parsing fails. Default: `false`

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
