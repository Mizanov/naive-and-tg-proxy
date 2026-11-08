#!/usr/bin/env bash
# ==============================================================================
# build-caddy.sh — Build custom Caddy v2 with NaiveProxy forwardproxy plugin
# ==============================================================================
set -euo pipefail

INFO="\033[1;34m[INFO]\033[0m"
OK="\033[1;32m[OK]\033[0m"
ERR="\033[1;31m[ERROR]\033[0m"

echo -e "${INFO} Checking Go toolchain..."
if ! command -v go >/dev/null 2>&1; then
    echo -e "${INFO} Installing Go compiler..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq golang git libcap2-bin
fi

GO_VERSION=$(go version | awk '{print $3}')
echo -e "${OK} Go is available: ${GO_VERSION}"

BUILD_DIR="/tmp/caddy-build-$$"
mkdir -p "$BUILD_DIR"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo -e "${INFO} Installing xcaddy builder..."
export GOPATH="$BUILD_DIR/go"
export PATH="$GOPATH/bin:$PATH"

go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest

echo -e "${INFO} Building hardened Caddy v2 with NaiveProxy plugin (klzgrad/forwardproxy@naive)..."
cd "$BUILD_DIR"
"$GOPATH/bin/xcaddy" build \
    --with github.com/caddyserver/forwardproxy@caddy2=github.com/klzgrad/forwardproxy@naive

if [ ! -f "$BUILD_DIR/caddy" ]; then
    echo -e "${ERR} Caddy binary build failed! Aborting."
    exit 1
fi

echo -e "${INFO} Installing Caddy binary to /usr/local/bin/caddy..."
install -m 755 "$BUILD_DIR/caddy" /usr/local/bin/caddy

echo -e "${INFO} Granting CAP_NET_BIND_SERVICE to Caddy..."
setcap cap_net_bind_service=+ep /usr/local/bin/caddy

CADDY_VER=$(/usr/local/bin/caddy version 2>&1 || true)
echo -e "${OK} Caddy installed successfully: ${CADDY_VER}"
