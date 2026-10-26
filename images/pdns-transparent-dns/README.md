# pdns-transparent-dns

`pdns-transparent-dns` is a small helper image for transparent DNS interception in front of a local PowerDNS Recursor.

The image does not run a DNS server itself. Instead, it prepares loopback takeover IPs and manages `iptables` rules so DNS traffic for selected IP addresses is accepted locally and can be handled by a recursor listening inside the same pod or network namespace.

## Purpose

This image is useful when a local PowerDNS Recursor should transparently receive DNS traffic that was originally addressed to:

- a dedicated local takeover IP
- optionally the cluster DNS Service IP
- optionally a primary PowerDNS Service IP

The implementation is intentionally minimal and based on Alpine, `iproute2`, and `iptables`.

## Included Scripts

The image ships two scripts:

- `/usr/local/bin/init.sh`
- `/usr/local/bin/interceptor.sh`

They are intended for different lifecycle phases.

### `init.sh`

`init.sh` ensures that the required `/32` takeover IP addresses exist on the loopback interface (`lo`).

It always prepares:

- `LOCAL_IP`

It can additionally prepare:

- `SERVICE_IP` when `TAKEOVER_CLUSTER_IP=true`
- `PRIMARY_SERVICE_IP` when that variable is set

This script is a good fit for an init container.

### `interceptor.sh`

`interceptor.sh` continuously watches whether a process is listening on `DNS_PORT` (default: `53`).

When a listener on `LOCAL_IP:DNS_PORT` is detected, it installs dedicated `iptables` rules and jump chains for the configured takeover IPs. When the listener disappears, it removes those rules again.

On `SIGTERM` it stops in two steps:

1. It removes the rules for `SERVICE_IP` at once. kube-proxy then sends that traffic to CoreDNS again.
2. It keeps the rules for the other takeover IPs until the recursor stops listening (at most `STOP_WAIT_SECONDS`). Then it removes all rules and the loopback takeover IPs.

For a stop without DNS errors, the recursor must answer for some seconds after the interceptor gets `SIGTERM`, for example with a `preStop` sleep on the recursor container.

This script is a good fit for a long-running sidecar or helper container.

## How It Works

At a high level, the container does the following:

1. Add one or more `/32` IP addresses to `lo`.
2. Wait until a local DNS listener is available on `DNS_PORT`.
3. Create custom `raw` and `filter` chains.
4. Add rules that:
   - mark DNS traffic as `NOTRACK` in the `raw` table
   - explicitly allow DNS traffic in the `filter` table
   - attach the custom chains through jumps in `PREROUTING`, `INPUT`, and `OUTPUT`
5. On stop, hand `SERVICE_IP` back to CoreDNS first, then remove all rules and takeover IPs when the recursor is gone.

The rules can cover both inbound traffic to the selected target IPs and, by default, matching output traffic as well.

## Runtime Configuration

### Required Variables

- `LOCAL_IP`: Local takeover IP that will be added to `lo` and always managed by the interceptor.
- `RAW_CHAIN`: Name of the custom `iptables` chain in the `raw` table.
- `FILTER_CHAIN`: Name of the custom `iptables` chain in the `filter` table.

### Optional Variables

- `DNS_PORT`: DNS listener port to watch. Default: `53`.
- `COMMENT_PREFIX`: Prefix used for `iptables` rule comments. Default: `PowerDNS transparent DNS`.
- `IPTABLES_WAIT_SECONDS`: Wait timeout passed to `iptables -w`. Default: `5`.
- `TAKEOVER_CLUSTER_IP`: When `true`, also manage `SERVICE_IP`. Default: `false`.
- `SERVICE_IP`: Cluster DNS Service IP to take over when `TAKEOVER_CLUSTER_IP=true`.
- `PRIMARY_SERVICE_IP`: Additional PowerDNS Service IP to manage.
- `SETUP_IPTABLES`: When `true`, install and remove `iptables` rules dynamically. Default: `true`.
- `CAPTURE_OUTPUT`: When `true`, also add `OUTPUT` rules for destination traffic. Default: `true`.
- `STOP_WAIT_SECONDS`: On `SIGTERM`, maximum time to wait for the recursor to stop before the remaining rules are removed. Keep it below the pod's termination grace period. Default: `20`.

## Operational Notes

- The container needs sufficient privileges to modify IP addresses and `iptables` rules. In Kubernetes this usually means `NET_ADMIN`.
- The image has no default `ENTRYPOINT` or `CMD`. You should explicitly run either `init.sh` or `interceptor.sh`.
- The image assumes another process is responsible for actually serving DNS on `DNS_PORT`.
- If `SETUP_IPTABLES=false`, the interceptor still validates the takeover IPs but does not manage rules.

## Example Usage Pattern

Typical deployment shape:

- an init container runs `/usr/local/bin/init.sh`
- the PowerDNS Recursor container starts and binds to the expected local address or port
- a helper container runs `/usr/local/bin/interceptor.sh`

This keeps the IP preparation separate from the long-running rule management loop.
