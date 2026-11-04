# openai-tunnel-client

`tunnel-client` packages the [OpenAI Secure MCP Tunnel client](https://github.com/openai/tunnel-client) as a Docker image for this repository's standard GitHub Actions publishing flow.

The image is built from the upstream GitHub tag declared in `images/openai-tunnel-client/Dockerfile`. When that version changes, the repository workflows build and publish a matching container image without needing a vendored checkout of the upstream source tree.

## Repository Fit

This image is intended to be built and published by the repository-wide GitHub Actions workflows in the repository root.

The image metadata and version tags are derived from `Dockerfile`.

## Runtime Behavior

The image entrypoint is:

```sh
/usr/local/bin/tunnel-client run
```

Override the default command or provide additional arguments in Helm or Kubernetes manifests when you want to pass explicit profile flags such as `--profile`, `--profile-file`, or `--config`.

The container exposes port `8080`, which matches the upstream daemon's operator endpoints such as `/healthz`, `/readyz`, `/metrics`, and `/ui`.

### MCP startup retries

The image sets `MCP_STARTUP_WAIT_TIMEOUT=5m` (upstream default: `0s`, disabled).
For the HTTP-streamable `main` MCP endpoint, this enables startup probe retries
when the endpoint refuses connections, its Unix socket is missing, or it returns
HTTP `429`/`5xx`. The first control-plane poll and OAuth discovery wait for the
initialization probe to complete.

Override the window with the `MCP_STARTUP_WAIT_TIMEOUT` environment variable or
the `--mcp.startup-wait-timeout` flag, for example `10m`. Set it to `0s` to restore
the upstream default. The image environment variable takes precedence over
`mcp.startup_wait_timeout` in YAML profiles; use an environment override or CLI
flag to change the image default.

This is a bounded startup wait, not an unlimited reconnect policy. If the window
expires, `/readyz` remains non-ready and the process can stay alive; restart the
container after the MCP endpoint becomes available. Initialization retries do
not replay tunneled MCP requests or provide recovery for existing MCP sessions
after a later server outage.

See the upstream [startup listener wait reference](https://github.com/openai/tunnel-client/blob/v0.0.17/docs/configuration.md#L609-L626)
and [startup troubleshooting](https://github.com/openai/tunnel-client/blob/v0.0.17/docs/troubleshooting.md#debug-why-readyz-is-failing).

## Version Tracking

The image version is controlled by `TUNNEL_CLIENT_VERSION` in `Dockerfile`.

That argument is annotated for Renovate with:

```text
# renovate: datasource=github-tags depName=openai/tunnel-client versioning=loose
```

This allows version bumps to follow upstream OpenAI tags, including the current non-standard tag format like `v0.0.9--context-conduit-topaz`.

## Local Validation

Build the image locally from the repository root:

```sh
docker build -t local/openai-tunnel-client:latest images/openai-tunnel-client
```
