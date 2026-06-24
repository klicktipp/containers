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
