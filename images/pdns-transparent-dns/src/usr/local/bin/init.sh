#!/bin/sh

set -eu

LOG_PREFIX="dns-interceptor-init"
. /usr/local/lib/pdns-transparent-dns/common.sh

ensure_local_ip "${LOCAL_IP}"
if [ "${TAKEOVER_CLUSTER_IP}" = "true" ]; then
	ensure_local_ip "${SERVICE_IP}"
	log "cluster DNS Service IP takeover prepared on ${SERVICE_IP}"
fi
