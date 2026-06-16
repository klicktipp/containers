# csa-exporter

`csa-exporter` is a small Prometheus exporter for the Certified Senders Alliance (CSA) API.

It fetches the latest available CSA metrics, converts them into Prometheus gauges, and exposes them on port `9100`.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Configuration

### Required Configuration

- `CSA_API_TOKEN`: Base64 token from the CSA UI, without the `ApiKey ` prefix.

Or:

- `CSA_API_ID`: Credential identifier, the left side of `id:secret`
- `CSA_API_SECRET`: API key secret

### Optional Configuration

- `CSA_API_URL`: CSA API base URL. Default: `https://monitor.certified-senders.org/api/v1`
- `CSA_API_TIMEOUT`: HTTP timeout in seconds. Default: `10`
- `LOG_LEVEL`: Python log level. Default: `INFO`
- `PORT`: HTTP listen port. Default: `9100`

If `CSA_API_TOKEN` is unset, the exporter builds the base64 token from `CSA_API_ID` and `CSA_API_SECRET` as `id:secret`.

If `CSA_API_TOKEN` is set, it always takes precedence.

In both cases, the exporter sends `Authorization: ApiKey <token>`.

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
CSA_API_TOKEN=your-base64-value make run
```

Or with a local env file:

```sh
printf 'CSA_API_TOKEN=your-base64-value\n' > .local.env
make run
```

Alternative if you have the two raw parts instead of the ready-made value:

```sh
printf 'CSA_API_ID=your-api-id\nCSA_API_SECRET=your-api-secret\n' > .local.env
make run
```
