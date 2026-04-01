#!/bin/sh

set -eu

LOG_PREFIX="dns-interceptor-init"
. /usr/local/lib/pdns-transparent-dns/common.sh

ensure_local_ip "${LOCAL_IP}"
if [ "${TAKEOVER_CLUSTER_IP}" = "true" ]; then
	ensure_local_ip "${SERVICE_IP}"
	log "cluster DNS Service IP takeover prepared on ${SERVICE_IP}"
fi

if [ -n "${PRIMARY_SERVICE_IP:-}" ]; then
	ensure_local_ip "${PRIMARY_SERVICE_IP}"
	log "primary PowerDNS Service IP prepared on ${PRIMARY_SERVICE_IP}"
fi
