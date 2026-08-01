#!/bin/zsh
# Resolve a commissionable Matter instance to an IP (via the Bonjour daemon —
# the only mDNS path macOS allows this context) and commission it by unicast.
# Usage: resolve_and_commission.sh <instance-name>
set -e
INSTANCE="$1"
DIR="$(cd "$(dirname "$0")" && pwd)"

SRV=$(script -q /dev/null perl -e 'alarm 5; exec "dns-sd","-L","'"$INSTANCE"'","_matterc._udp","local."' 2>/dev/null | grep "can be reached at" | head -1)
echo "SRV: $SRV"
HOSTPORT=$(echo "$SRV" | sed -E 's/.*can be reached at ([^ ]+).*/\1/')
HOST="${HOSTPORT%:*}"
echo "host: $HOST"

IP=$(script -q /dev/null perl -e 'alarm 5; exec "dns-sd","-G","v4","'"$HOST"'"' 2>/dev/null | grep " Add " | head -1 | awk '{print $6}')
echo "ip: $IP"
[ -n "$IP" ] || { echo "RESOLVE_FAILED"; exit 1; }

exec "$DIR/venv/bin/python" "$DIR/commission.py" "$IP"
