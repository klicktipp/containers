# image-version: CSA_EXPORTER_VERSION
ARG CSA_EXPORTER_VERSION=0.2.2
ARG IMAGE_DESCRIPTION="Certified Senders Alliance Prometheus exporter"
ARG IMAGE_AUTHORS="KlickTipp"
ARG IMAGE_VENDOR="KlickTipp"
ARG IMAGE_SOURCE="https://github.com/klicktipp/containers"

FROM python:3.14-slim

ARG CSA_EXPORTER_VERSION
ARG IMAGE_DESCRIPTION
ARG IMAGE_AUTHORS
ARG IMAGE_VENDOR
ARG IMAGE_SOURCE

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system app \
    && adduser --system --ingroup app app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

LABEL org.opencontainers.image.authors="${IMAGE_AUTHORS}" \
      org.opencontainers.image.description="${IMAGE_DESCRIPTION}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.vendor="${IMAGE_VENDOR}" \
      org.opencontainers.image.version="${CSA_EXPORTER_VERSION}"

COPY rootfs/ /

USER app

EXPOSE 9100

ENTRYPOINT ["python3", "/usr/local/bin/exporter.py"]
