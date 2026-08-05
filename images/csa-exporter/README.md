# csa-exporter

`csa-exporter` is a small Prometheus exporter for the Certified Senders Alliance (CSA) API.

It fetches the latest available CSA metrics, converts them into Prometheus gauges, and exposes them on port `9100`.

The exporter collects KPI details grouped by sending IP, DKIM domain, and
header-from domain. It also exports the CSA spam complaint rate as the reported
minimum, average, and maximum plus the underlying email volume for each scope.

## Complaint Metrics

- `csa_spam_complaint_rate{scope="global",entity="global",statistic}`:
  CSA-provided global complaint rate; `statistic` is `min`, `avg`, or `max`.
- `csa_spam_complaint_total_volume{scope,entity}`: Email volume underlying the
  global complaint-rate calculation.
- `csa_ip_spam_click_ratio{ip}`: Reported complaint ratio per sending IP.
- `csa_dkim_spam_click_ratio{domain}`: Reported complaint ratio per DKIM
  domain.
- `csa_from_domain_spam_click_ratio{domain}`: Reported complaint ratio per
  header-from domain.

The scoped `/stat/spamclickrate/{scope}` detail endpoints currently return HTTP
404 for `ip`, `dkimdomain`, and `fromdomain` with production API credentials.
The exporter therefore uses the working KPI endpoints for those scopes and
avoids repeated failing requests.

The CSA API does not provide an exact absolute mailbox-provider complaint count
in these responses. The exporter intentionally does not derive one by
multiplying an aggregate rate by volume because that would not be guaranteed to
represent an exact count. The separate `csa_complaints` API describes legal CSA
complaints and cases and is not treated as the spam complaint rate.

`csa_legal_complaints{scope,brand,kind}` separately exposes exact absolute CSA
legal complaint and case counts. It contains only aggregate global or brand
counts and never exports complainants, subjects, report IDs, or other complaint
record details.

KPI metrics additionally expose email volume, alignment, DKIM errors, missing
DKIM, spam traps, complaint ratio, and the CSA-limit status for IPs and domains.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Configuration

### Required Configuration

- `CSA_API_TOKEN`: Base64 token from the CSA UI, without the `ApiKey` prefix.

Or:

- `CSA_API_ID`: Credential identifier, the left side of `id:secret`
- `CSA_API_SECRET`: API key secret

### Optional Configuration

- `CSA_API_URL`: CSA API base URL. Default: `https://monitor.certified-senders.org/api/v1`
- `CSA_API_TIMEOUT`: HTTP timeout in seconds. Default: `10`
- `CSA_COMPLAINT_LOOKBACK_DAYS`: Lookback for legal complaint counts grouped by
  brand. Default: `30`
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
