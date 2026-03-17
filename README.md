# docker-images

This repository contains Docker image definitions intended for public distribution.

## Structure

- `images/<name>/Dockerfile`: one image per subdirectory
- `.github/workflows/build.yml`: GitHub Actions workflow for building selected images

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

The GitHub Actions workflow is prepared to build and publish changed images. Registry and tagging behavior can be adjusted as needed.
