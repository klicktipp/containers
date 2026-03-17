# docker-images

This repository contains Docker image definitions intended for public distribution.

## Structure

- `images/<name>/Dockerfile`: one image per subdirectory
- `.github/workflows/build.yml`: pull request and main branch build workflow
- `.github/workflows/release.yml`: release workflow for full image publishing and release notes

## Version tags

Version tags are derived from the image Dockerfile.

- Add `# image-version: <ARG_NAME>` above the primary version argument when the image should publish version tags
- Define that argument as `ARG <ARG_NAME>=...`
- The workflow publishes `latest`, `sha-...`, the full version, and a major-minor tag when a primary version is available

## Local build

Build a specific image locally by pointing Docker to the corresponding subdirectory.

Example:

```sh
docker build -t local/kubectl:latest images/kubectl
```

## Publishing

On `main`, the build workflow publishes only changed images.

On published GitHub releases, the release workflow builds all images, applies the release tag in addition to the image version tags, and updates the release notes with image-specific changes since the previous release.
