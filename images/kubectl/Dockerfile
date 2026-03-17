FROM curlimages/curl:8.18.0 AS fetcher

# image-version: KUBECTL_VERSION
# renovate: datasource=github-releases depName=kubernetes/kubernetes
ARG KUBECTL_VERSION=v1.31.0
ARG TARGETARCH

RUN curl --fail --silent --show-error --location --output /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && chmod 0755 /tmp/kubectl

FROM scratch

COPY --from=fetcher /tmp/kubectl /usr/local/bin/kubectl
COPY --from=fetcher /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

ENTRYPOINT ["/usr/local/bin/kubectl"]
