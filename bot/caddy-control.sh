#!/usr/bin/env bash
# ==============================================================================
# bot/caddy-control.sh — Hardened, Restricted Privileged Helper for Caddy & Docker
# ==============================================================================
set -euo pipefail

ACTION="${1:-}"

validate_canonical_path() {
    local target="$1"
    if [ -z "$target" ]; then
        echo "ERROR: Missing target path parameter." >&2
        exit 1
    fi
    if [[ "$target" == *..* ]] || [[ "$target" =~ [[:cntrl:]] ]]; then
        echo "SECURITY ERROR: Path traversal attempt detected: $target" >&2
        exit 1
    fi
    if [ ! -f "$target" ]; then
        echo "ERROR: Target file does not exist: $target" >&2
        exit 1
    fi
}

validate_identifier() {
    local id="$1"
    if ! [[ "$id" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo "SECURITY ERROR: Invalid container identifier: $id" >&2
        exit 1
    fi
}

case "$ACTION" in
    caddy-validate)
        TARGET="${2:-}"
        validate_canonical_path "$TARGET"
        exec /usr/local/bin/caddy validate --config "$TARGET"
        ;;
    caddy-reload)
        if command -v systemctl >/dev/null 2>&1; then
            exec systemctl reload caddy
        else
            exec /usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
        fi
        ;;
    caddy-status)
        exec systemctl is-active caddy
        ;;
    docker-status)
        CONTAINER="${2:-}"
        validate_identifier "$CONTAINER"
        exec /usr/bin/docker inspect -f '{{.State.Status}}' "$CONTAINER"
        ;;
    tgproxy-link)
        exec /usr/bin/docker exec tgweb-proxy show-link
        ;;
    *)
        echo "ERROR: Unknown or unsupported action: '$ACTION'" >&2
        echo "Allowed actions: caddy-validate <path>, caddy-reload, caddy-status, docker-status <container>, tgproxy-link" >&2
        exit 1
        ;;
esac
