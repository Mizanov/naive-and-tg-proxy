#!/usr/bin/env bash
# ==============================================================================
# scripts/setup-firewall.sh — Strict UFW Configuration
# ==============================================================================
set -euo pipefail

echo "Configuring UFW firewall for Stealth Proxy Hub..."
ufw default deny incoming
ufw default allow outgoing

# Strict minimum allowed ports: SSH, HTTP (ACME), HTTPS (Caddy Naive & TG Proxy)
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP (ACME Challenge)'
ufw allow 443/tcp comment 'HTTPS (Caddy: NaiveProxy & TG Web Proxy)'

ufw --force enable
ufw status verbose
