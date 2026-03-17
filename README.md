# docker-images

This repository contains Docker image definitions intended for public distribution.

## Structure

- `images/<name>/Dockerfile`: one image per subdirectory
- `.github/workflows/build.yml`: pull request build validation workflow
- `.github/workflows/push.yml`: main branch and manual publishing workflow
- `.github/workflows/release.yml`: automatic per-image GitHub release workflow after successful image publishing

## Metadata

Version tags and image metadata are derived from the image Dockerfile.

- Add `# image-version: <ARG_NAME>` above the primary version argument when the image should publish version tags
- Define `ARG IMAGE_DESCRIPTION=...`, `ARG IMAGE_AUTHORS=...`, `ARG IMAGE_VENDOR=...`, and `ARG IMAGE_SOURCE=...` when the image should publish OCI metadata
- Define that argument as `ARG <ARG_NAME>=...`
- The workflow publishes `latest`, `sha-...`, the full version, and a major-minor tag when a primary version is available

## Local build

Build a specific image locally by pointing Docker to the corresponding subdirectory.

Example:

```sh
docker build -t local/kubectl:latest images/kubectl
```

## Publishing

On pull requests, the build workflow validates only the changed images without publishing them.
The stable status check for branch protection is `build-validation`.

On `main` and manual runs, the push workflow publishes only changed images.

After a successful publishing run, the release workflow creates or updates one GitHub release per changed image using the image version and includes only changes from that image directory since the previous release for that image.
