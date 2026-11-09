#!/usr/bin/env bash
# ==============================================================================
# scripts/setup-security.sh — Linux Hardening, Fail2ban & Sysctl Tuning
# ==============================================================================
set -euo pipefail

INFO="\033[1;34m[INFO]\033[0m"
OK="\033[1;32m[OK]\033[0m"

echo -e "${INFO} Applying kernel network stack hardening, IPv6 leak protection & BBR..."
cat << 'EOF' > /etc/sysctl.d/99-stealth-security.conf
# Disable IPv6 system-wide (Prevents IP/DNS leaks, asymmetric routing & dual-stack issues)
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1

# Enable BBR Congestion Control
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# TCP SYN Flood Protection
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_syn_retries = 2
net.ipv4.tcp_synack_retries = 2
net.ipv4.tcp_max_syn_backlog = 8192

# Reverse Path Filtering (Spoofing Protection)
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Ignore ICMP Broadcasts & Bogus Error Responses
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1

# Buffer & Socket Scaling
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.ipv4.tcp_rmem = 4096 87380 33554432
net.ipv4.tcp_wmem = 4096 65536 33554432
net.core.somaxconn = 16384
net.ipv4.tcp_max_tw_buckets = 2000000
net.ipv4.tcp_fastopen = 3

# File Descriptors Limit
fs.file-max = 2097152
EOF

sysctl --system >/dev/null 2>&1 || true
if [ -f /etc/default/ufw ]; then
    sed -i 's/^IPV6=yes/IPV6=no/' /etc/default/ufw 2>/dev/null || true
fi
echo -e "${OK} Kernel parameters applied (IPv6 disabled, BBR active)."

echo -e "${INFO} Installing and configuring Fail2ban with UFW banaction..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban python3-systemd

cat << 'EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime  = 1d
findtime = 10m
maxretry = 5
banaction = ufw
banaction_allports = ufw

[sshd]
enabled = true
port    = 22
mode    = aggressive
backend = systemd
EOF

systemctl daemon-reload
systemctl enable --now fail2ban
systemctl restart fail2ban
echo -e "${OK} Fail2ban active with 24-hour ban for SSH brute force."

# SSH Hardening (Enforcing Key-Only Authentication, Disabling Passwords)
echo -e "${INFO} Configuring SSH security (Enforcing key-only authentication, disabling passwords)..."
mkdir -p /etc/ssh/sshd_config.d

if [ -s /root/.ssh/authorized_keys ]; then
    cat << 'EOF' > /etc/ssh/sshd_config.d/01-security.conf
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
EOF
    # Ensure no existing configuration file overrides PasswordAuthentication to yes
    sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config.d/*.conf /etc/ssh/sshd_config 2>/dev/null || true

    if sshd -t >/dev/null 2>&1; then
        systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
        echo -e "${OK} SSH configured: Key-only authentication strictly enforced (PasswordAuthentication=no)."
    else
        echo -e "${INFO} sshd configuration test failed; changes not reloaded to avoid lockout."
    fi
else
    echo -e "${INFO} No keys found in /root/.ssh/authorized_keys! Keeping PasswordAuthentication enabled temporarily to prevent lockout. Add your SSH public key and re-run this script."
fi
