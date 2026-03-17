FROM curlimages/curl:8.18.0 AS fetcher

# image-version: KUBECTL_VERSION
# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=v1.33.9
ARG TARGETARCH
ARG IMAGE_DESCRIPTION="Kubernetes command-line tool (kubectl)"
ARG IMAGE_AUTHORS="KlickTipp"
ARG IMAGE_VENDOR="KlickTipp"
ARG IMAGE_SOURCE="https://github.com/klicktipp/containers"

RUN curl --fail --silent --show-error --location --output /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && chmod 0755 /tmp/kubectl

FROM alpine:3.23

ARG IMAGE_DESCRIPTION
ARG IMAGE_AUTHORS
ARG IMAGE_VENDOR
ARG IMAGE_SOURCE

RUN apk add --no-cache ca-certificates

LABEL org.opencontainers.image.authors="${IMAGE_AUTHORS}" \
      org.opencontainers.image.description="${IMAGE_DESCRIPTION}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.vendor="${IMAGE_VENDOR}"

COPY --from=fetcher /tmp/kubectl /usr/local/bin/kubectl

ENTRYPOINT ["/usr/local/bin/kubectl"]
