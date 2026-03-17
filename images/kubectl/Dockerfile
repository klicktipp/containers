FROM debian:13.4-slim AS fetcher

ARG KUBECTL_VERSION=v1.30.3
ARG TARGETARCH

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates wget \
    && wget --quiet --output-document /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM scratch

COPY --from=fetcher /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --from=fetcher /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

ENTRYPOINT ["/usr/local/bin/kubectl"]
