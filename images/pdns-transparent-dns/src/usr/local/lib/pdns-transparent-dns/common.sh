#!/bin/sh

set -eu

DNS_PORT="${DNS_PORT:-53}"
COMMENT_PREFIX="${COMMENT_PREFIX:-PowerDNS transparent DNS}"
IPTABLES_WAIT_SECONDS="${IPTABLES_WAIT_SECONDS:-5}"
TAKEOVER_CLUSTER_IP="${TAKEOVER_CLUSTER_IP:-false}"
PRIMARY_SERVICE_IP="${PRIMARY_SERVICE_IP:-}"
ADDITIONAL_SERVICE_IP="${ADDITIONAL_SERVICE_IP:-}"
SETUP_IPTABLES="${SETUP_IPTABLES:-true}"
CAPTURE_OUTPUT="${CAPTURE_OUTPUT:-true}"

log() {
	prefix="${LOG_PREFIX:-dns-interceptor}"
	echo "${prefix}: $*"
}

ipt() {
	iptables -w "${IPTABLES_WAIT_SECONDS}" "$@"
}

has_local_ip() {
	ip_addr="$1"
	ip -o -4 addr show dev lo 2>/dev/null | grep -Eq "[[:space:]]${ip_addr}/32([[:space:]]|$)"
}

ensure_local_ip() {
	ip_addr="$1"
	if has_local_ip "${ip_addr}"; then
		log "ip ${ip_addr}/32 already present on lo"
		return 0
	fi
	log "adding ip ${ip_addr}/32 to lo"
	ip addr add "${ip_addr}/32" dev lo
	if ! has_local_ip "${ip_addr}"; then
		echo "failed to bind ${ip_addr}/32 on lo" >&2
		ip -o addr show dev lo >&2 || true
		return 1
	fi
	log "ip ${ip_addr}/32 added to lo"
}

remove_local_ip() {
	ip_addr="$1"
	if has_local_ip "${ip_addr}"; then
		log "removing ip ${ip_addr}/32 from lo"
		ip addr del "${ip_addr}/32" dev lo || true
	fi
}

require_local_ip() {
	ip_addr="$1"
	if ! has_local_ip "${ip_addr}"; then
		echo "required takeover ip ${ip_addr}/32 is not present on lo" >&2
		ip -o addr show dev lo >&2 || true
		exit 1
	fi
	log "confirmed ip ${ip_addr}/32 on lo"
}

is_recursor_ready() {
	ss -H -ltnu 2>/dev/null | grep -Eq "(^udp|^tcp).*:${DNS_PORT}[[:space:]]"
}

ensure_raw_chain() {
	ipt -t raw -N "${RAW_CHAIN}" 2>/dev/null || true
	ipt -t raw -F "${RAW_CHAIN}"
}

ensure_filter_chain() {
	ipt -t filter -N "${FILTER_CHAIN}" 2>/dev/null || true
	ipt -t filter -F "${FILTER_CHAIN}"
}

add_ip_rules() {
	target_ip="$1"
	ipt -t raw -A "${RAW_CHAIN}" -d "${target_ip}" -p udp --dport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: skip conntrack" -j NOTRACK
	ipt -t raw -A "${RAW_CHAIN}" -d "${target_ip}" -p tcp --dport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: skip conntrack" -j NOTRACK
	if [ "${CAPTURE_OUTPUT}" = "true" ]; then
		ipt -t raw -A "${RAW_CHAIN}" -s "${target_ip}" -p udp --sport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: skip conntrack" -j NOTRACK
		ipt -t raw -A "${RAW_CHAIN}" -s "${target_ip}" -p tcp --sport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: skip conntrack" -j NOTRACK
	fi
	ipt -t filter -A "${FILTER_CHAIN}" -d "${target_ip}" -p udp --dport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: allow DNS traffic" -j ACCEPT
	ipt -t filter -A "${FILTER_CHAIN}" -d "${target_ip}" -p tcp --dport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: allow DNS traffic" -j ACCEPT
	ipt -t filter -A "${FILTER_CHAIN}" -s "${target_ip}" -p udp --sport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: allow DNS traffic" -j ACCEPT
	ipt -t filter -A "${FILTER_CHAIN}" -s "${target_ip}" -p tcp --sport "${DNS_PORT}" -m comment --comment "${COMMENT_PREFIX}: allow DNS traffic" -j ACCEPT
}

ensure_jump() {
	table_name="$1"
	proto="$2"
	table_chain="$3"
	target_chain="$4"
	match_direction="$5"
	match_ip="$6"
	case "${match_direction}" in
	destination)
		shift_args="-d ${match_ip} --dport ${DNS_PORT}"
		;;
	source)
		shift_args="-s ${match_ip} --sport ${DNS_PORT}"
		;;
	*)
		echo "unsupported jump match direction: ${match_direction}" >&2
		exit 1
		;;
	esac
	# shellcheck disable=SC2086
	ipt -t "${table_name}" -C "${table_chain}" -p "${proto}" ${shift_args} -m comment --comment "${COMMENT_PREFIX}: jump" -j "${target_chain}" 2>/dev/null ||
		ipt -t "${table_name}" -I "${table_chain}" 1 -p "${proto}" ${shift_args} -m comment --comment "${COMMENT_PREFIX}: jump" -j "${target_chain}"
}

remove_jump() {
	table_name="$1"
	proto="$2"
	table_chain="$3"
	target_chain="$4"
	match_direction="$5"
	match_ip="$6"
	case "${match_direction}" in
	destination)
		shift_args="-d ${match_ip} --dport ${DNS_PORT}"
		;;
	source)
		shift_args="-s ${match_ip} --sport ${DNS_PORT}"
		;;
	*)
		echo "unsupported jump match direction: ${match_direction}" >&2
		exit 1
		;;
	esac
	# shellcheck disable=SC2086
	while ipt -t "${table_name}" -C "${table_chain}" -p "${proto}" ${shift_args} -m comment --comment "${COMMENT_PREFIX}: jump" -j "${target_chain}" 2>/dev/null; do
		ipt -t "${table_name}" -D "${table_chain}" -p "${proto}" ${shift_args} -m comment --comment "${COMMENT_PREFIX}: jump" -j "${target_chain}" || true
	done
}

install_ip_rules_and_jumps() {
	target_ip="$1"
	add_ip_rules "${target_ip}"
	ensure_jump raw udp PREROUTING "${RAW_CHAIN}" destination "${target_ip}"
	ensure_jump raw tcp PREROUTING "${RAW_CHAIN}" destination "${target_ip}"
	if [ "${CAPTURE_OUTPUT}" = "true" ]; then
		ensure_jump raw udp OUTPUT "${RAW_CHAIN}" destination "${target_ip}"
		ensure_jump raw tcp OUTPUT "${RAW_CHAIN}" destination "${target_ip}"
	fi
	ensure_jump raw udp OUTPUT "${RAW_CHAIN}" source "${target_ip}"
	ensure_jump raw tcp OUTPUT "${RAW_CHAIN}" source "${target_ip}"
	ensure_jump filter udp INPUT "${FILTER_CHAIN}" destination "${target_ip}"
	ensure_jump filter tcp INPUT "${FILTER_CHAIN}" destination "${target_ip}"
	ensure_jump filter udp OUTPUT "${FILTER_CHAIN}" source "${target_ip}"
	ensure_jump filter tcp OUTPUT "${FILTER_CHAIN}" source "${target_ip}"
}

remove_ip_rules_and_jumps() {
	target_ip="$1"
	remove_jump raw udp PREROUTING "${RAW_CHAIN}" destination "${target_ip}"
	remove_jump raw tcp PREROUTING "${RAW_CHAIN}" destination "${target_ip}"
	if [ "${CAPTURE_OUTPUT}" = "true" ]; then
		remove_jump raw udp OUTPUT "${RAW_CHAIN}" destination "${target_ip}"
		remove_jump raw tcp OUTPUT "${RAW_CHAIN}" destination "${target_ip}"
	fi
	remove_jump raw udp OUTPUT "${RAW_CHAIN}" source "${target_ip}"
	remove_jump raw tcp OUTPUT "${RAW_CHAIN}" source "${target_ip}"
	remove_jump filter udp INPUT "${FILTER_CHAIN}" destination "${target_ip}"
	remove_jump filter tcp INPUT "${FILTER_CHAIN}" destination "${target_ip}"
	remove_jump filter udp OUTPUT "${FILTER_CHAIN}" source "${target_ip}"
	remove_jump filter tcp OUTPUT "${FILTER_CHAIN}" source "${target_ip}"
}

install_rules() {
	service_ip_active="$1"
	primary_service_ip_active="$2"
	additional_service_ip_active="$3"
	ensure_raw_chain
	ensure_filter_chain
	install_ip_rules_and_jumps "${LOCAL_IP}"
	if [ "${service_ip_active}" -eq 1 ]; then
		install_ip_rules_and_jumps "${SERVICE_IP}"
	fi
	if [ "${primary_service_ip_active}" -eq 1 ]; then
		install_ip_rules_and_jumps "${PRIMARY_SERVICE_IP}"
	fi
	if [ "${additional_service_ip_active}" -eq 1 ]; then
		install_ip_rules_and_jumps "${ADDITIONAL_SERVICE_IP}"
	fi
}

remove_rules() {
	remove_ip_rules_and_jumps "${LOCAL_IP}"
	if [ "${TAKEOVER_CLUSTER_IP}" = "true" ] && [ -n "${SERVICE_IP:-}" ]; then
		remove_ip_rules_and_jumps "${SERVICE_IP}"
	fi
	if [ -n "${PRIMARY_SERVICE_IP}" ]; then
		remove_ip_rules_and_jumps "${PRIMARY_SERVICE_IP}"
	fi
	if [ -n "${ADDITIONAL_SERVICE_IP}" ]; then
		remove_ip_rules_and_jumps "${ADDITIONAL_SERVICE_IP}"
	fi
	ipt -t raw -F "${RAW_CHAIN}" 2>/dev/null || true
	ipt -t raw -X "${RAW_CHAIN}" 2>/dev/null || true
	ipt -t filter -F "${FILTER_CHAIN}" 2>/dev/null || true
	ipt -t filter -X "${FILTER_CHAIN}" 2>/dev/null || true
}

remove_takeover_ips() {
	remove_local_ip "${LOCAL_IP}"
	if [ "${TAKEOVER_CLUSTER_IP}" = "true" ] && [ -n "${SERVICE_IP:-}" ]; then
		remove_local_ip "${SERVICE_IP}"
	fi
	if [ -n "${PRIMARY_SERVICE_IP}" ]; then
		remove_local_ip "${PRIMARY_SERVICE_IP}"
	fi
	if [ -n "${ADDITIONAL_SERVICE_IP}" ]; then
		remove_local_ip "${ADDITIONAL_SERVICE_IP}"
	fi
}
