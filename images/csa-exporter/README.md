# csa-exporter

`csa-exporter` is a small Prometheus exporter for the Certified Senders Alliance (CSA) API.

It fetches the latest available CSA metrics, converts them into Prometheus gauges, and exposes them on port `9100`.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Configuration

### Required Configuration

- `CSA_API_KEY`: API key used for the `Authorization: ApiKey ...` header.

### Optional Configuration

- `CSA_API_URL`: CSA API base URL. Default: `https://monitor.certified-senders.org/api/v1`
- `CSA_API_TIMEOUT`: HTTP timeout in seconds. Default: `10`
- `LOG_LEVEL`: Python log level. Default: `INFO`
- `PORT`: HTTP listen port. Default: `9100`

If `CSA_API_KEY` is unset, the exporter still starts but logs an authentication warning and upstream requests will fail until a valid key is provided.

## Exposed Endpoints

- `/metrics`: Prometheus metrics endpoint
- `/healthz`: Returns `200` while the process is healthy
- `/livez`: Basic liveness endpoint

## Local Validation

Run the local checks from the image directory:

```sh
make check
```

Build only:

```sh
docker build -t local/csa-exporter:latest .
```

Run with a direct environment override:

```sh
CSA_API_KEY=your-key make run
```

Or with a local env file:

```sh
printf 'CSA_API_KEY=your-key\n' > .local.env
make run
```
