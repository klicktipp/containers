# image-version: DNS_INTERCEPTOR_VERSION
ARG DNS_INTERCEPTOR_VERSION=0.1.2
# renovate: datasource=docker depName=alpine
ARG ALPINE_VERSION=3.23
ARG IMAGE_DESCRIPTION="Transparent DNS interceptor"
ARG IMAGE_AUTHORS="KlickTipp"
ARG IMAGE_VENDOR="KlickTipp"
ARG IMAGE_SOURCE="https://github.com/klicktipp/containers"

FROM alpine:${ALPINE_VERSION}

ARG DNS_INTERCEPTOR_VERSION
ARG IMAGE_DESCRIPTION
ARG IMAGE_AUTHORS
ARG IMAGE_VENDOR
ARG IMAGE_SOURCE

RUN apk add --no-cache iproute2 iptables

LABEL org.opencontainers.image.authors="${IMAGE_AUTHORS}" \
      org.opencontainers.image.description="${IMAGE_DESCRIPTION}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.vendor="${IMAGE_VENDOR}" \
      org.opencontainers.image.version="${DNS_INTERCEPTOR_VERSION}"

COPY ./src/ /

RUN chmod 0755 /usr/local/bin/init.sh /usr/local/bin/interceptor.sh
