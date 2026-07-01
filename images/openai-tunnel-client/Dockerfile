# image-version: TUNNEL_CLIENT_VERSION
# renovate: datasource=github-tags depName=openai/tunnel-client versioning=loose
ARG TUNNEL_CLIENT_VERSION=v0.0.9--context-conduit-topaz
ARG GO_BUILDER_IMAGE=golang:1.26.2-alpine
ARG UI_BUILDER_IMAGE=node:22-alpine
ARG RUNTIME_IMAGE=alpine:3.24
ARG PNPM_FALLBACK_VERSION=pnpm@11.4.0+sha512.f0febc7e37552ab485494a914241b338e0b3580b93d54ce31f00933015880863129038a1b4ae4e414a0ee63ac35bf21197e990172c4a68256450b5636310968f
ARG IMAGE_DESCRIPTION="OpenAI Secure MCP Tunnel client"
ARG IMAGE_AUTHORS="KlickTipp"
ARG IMAGE_VENDOR="KlickTipp"
ARG IMAGE_SOURCE="https://github.com/klicktipp/containers"

FROM alpine:3.24 AS source

ARG TUNNEL_CLIENT_VERSION

WORKDIR /src

SHELL ["/bin/ash", "-eo", "pipefail", "-c"]

RUN apk add --no-cache curl tar

RUN curl --fail --silent --show-error --location \
    "https://codeload.github.com/openai/tunnel-client/tar.gz/refs/tags/${TUNNEL_CLIENT_VERSION}" \
    | tar -xz --strip-components=1

FROM ${UI_BUILDER_IMAGE} AS ui-builder

ARG PNPM_FALLBACK_VERSION

WORKDIR /src

COPY --from=source /src /src

RUN package_manager="$(node -e 'const fs = require("fs"); const path = "/src/package.json"; if (fs.existsSync(path)) { const pkg = JSON.parse(fs.readFileSync(path, "utf8")); process.stdout.write(pkg.packageManager || ""); }')" \
    && corepack enable pnpm \
    && corepack prepare "${package_manager:-${PNPM_FALLBACK_VERSION}}" --activate \
    && CI=true pnpm --dir adminui install --frozen-lockfile --config.shared-workspace-lockfile=false --config.confirmModulesPurge=false \
    && pnpm --dir adminui build

FROM ${GO_BUILDER_IMAGE} AS builder

ARG TUNNEL_CLIENT_VERSION

WORKDIR /src

COPY --from=source /src /src
COPY --from=ui-builder /src/pkg/adminui/assets /src/pkg/adminui/assets

RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build \
    -trimpath \
    -ldflags "-s -w -X go.openai.org/api/tunnel-client/pkg/version.GitSHA=${TUNNEL_CLIENT_VERSION}" \
    -o /usr/local/bin/tunnel-client ./cmd/client

FROM ${RUNTIME_IMAGE}

ARG TUNNEL_CLIENT_VERSION
ARG IMAGE_DESCRIPTION
ARG IMAGE_AUTHORS
ARG IMAGE_VENDOR
ARG IMAGE_SOURCE

WORKDIR /app

RUN apk add --no-cache ca-certificates

LABEL org.opencontainers.image.authors="${IMAGE_AUTHORS}" \
      org.opencontainers.image.description="${IMAGE_DESCRIPTION}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.vendor="${IMAGE_VENDOR}" \
      org.opencontainers.image.version="${TUNNEL_CLIENT_VERSION}"

COPY --from=builder /usr/local/bin/tunnel-client /usr/local/bin/tunnel-client

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/tunnel-client", "run"]
