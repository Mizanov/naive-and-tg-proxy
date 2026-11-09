#!/usr/bin/env bash
# ==============================================================================
# scripts/setup-firewall.sh — Strict UFW Configuration
# ==============================================================================
set -euo pipefail

echo "Configuring UFW firewall for Stealth Proxy Hub..."
if [ -f /etc/default/ufw ]; then
    sed -i 's/^IPV6=yes/IPV6=no/' /etc/default/ufw 2>/dev/null || true
fi
ufw default deny incoming
ufw default allow outgoing

# Strict minimum allowed ports: SSH, HTTP, HTTPS
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'

ufw --force enable
ufw status verbose
