#!/bin/sh

set -eu

LOG_PREFIX="dns-interceptor"
. /usr/local/lib/pdns-transparent-dns/common.sh

service_ip_active=0
primary_service_ip_active=0
additional_service_ip_active=0
rules_active=0
cleaned_up=0

cleanup() {
	if [ "${cleaned_up}" -eq 1 ]; then
		return 0
	fi
	cleaned_up=1
	if [ "${SETUP_IPTABLES}" = "true" ]; then
		log "cleaning up local dns takeover rules"
		remove_rules
	fi
	remove_takeover_ips
}

# Stop order: hand the cluster DNS Service IP back to CoreDNS first, keep the other
# takeover IPs while the recursor still answers (its preStop delays its stop), then clean up.
signal_handler() {
	trap '' TERM INT
	if [ "${rules_active}" -eq 1 ] && [ "${service_ip_active}" -eq 1 ]; then
		log "stop requested, releasing cluster DNS Service IP ${SERVICE_IP} to CoreDNS"
		release_service_ip
		log "cluster DNS Service IP ${SERVICE_IP} released, waiting for recursor to stop"
		wait_for_recursor_stop
	fi
	cleanup
	exit 0
}

trap signal_handler TERM INT
trap cleanup EXIT

require_local_ip "${LOCAL_IP}"
if [ "${TAKEOVER_CLUSTER_IP}" = "true" ]; then
	if has_local_ip "${SERVICE_IP}"; then
		service_ip_active=1
		log "cluster DNS Service IP takeover confirmed on ${SERVICE_IP}"
	else
		echo "warning: cluster DNS Service IP ${SERVICE_IP}/32 is not present on lo, continuing with localIP ${LOCAL_IP} only" >&2
	fi
fi

if [ -n "${PRIMARY_SERVICE_IP:-}" ]; then
	if has_local_ip "${PRIMARY_SERVICE_IP}"; then
		primary_service_ip_active=1
		log "primary PowerDNS Service IP confirmed on ${PRIMARY_SERVICE_IP}"
	else
		echo "warning: primary PowerDNS Service IP ${PRIMARY_SERVICE_IP}/32 is not present on lo" >&2
	fi
fi

if [ -n "${ADDITIONAL_SERVICE_IP:-}" ]; then
	if has_local_ip "${ADDITIONAL_SERVICE_IP}"; then
		additional_service_ip_active=1
		log "additional PowerDNS Service IP confirmed on ${ADDITIONAL_SERVICE_IP}"
	else
		echo "warning: additional PowerDNS Service IP ${ADDITIONAL_SERVICE_IP}/32 is not present on lo" >&2
	fi
fi

while true; do
	if [ "${SETUP_IPTABLES}" = "true" ] && is_recursor_ready; then
		if [ "${rules_active}" -eq 0 ]; then
			log "recursor listener detected on port ${DNS_PORT}, installing takeover rules"
			install_rules "${service_ip_active}" "${primary_service_ip_active}" "${additional_service_ip_active}"
			rules_active=1
		fi
	else
		if [ "${rules_active}" -eq 1 ]; then
			log "recursor listener no longer detected on port ${DNS_PORT}, removing takeover rules"
			remove_rules
			rules_active=0
		fi
	fi
	# Wait in the background so that TERM runs the handler at once.
	sleep 2 &
	wait $! || true
done
