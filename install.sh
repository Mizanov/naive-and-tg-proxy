#!/usr/bin/env bash
# ==============================================================================
# install.sh — Turnkey Deployment of Stealth Proxy Hub & Telegram Web Proxy
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFO="\033[1;34m[INFO]\033[0m"
OK="\033[1;32m[OK]\033[0m"
WARN="\033[1;33m[WARN]\033[0m"
ERR="\033[1;31m[ERROR]\033[0m"
NC="\033[0m"
BOLD="\033[1m"

echo -e "${BOLD}"
echo "═════════════════════════════════════════════════════════════════════"
echo "  🛡️ Stealth Proxy & Telegram Web Proxy Hub — Turnkey Deployment     "
echo "═════════════════════════════════════════════════════════════════════"
echo -e "${NC}"

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${ERR} Этот скрипт должен быть запущен с правами root!"
    exit 1
fi

prompt_var() {
    local var_name="$1"
    local prompt_text="$2"
    local default_val="${3:-}"
    local val=""

    if [ -n "$default_val" ]; then
        read -r -p "$prompt_text [$default_val]: " val
        val="${val:-$default_val}"
    else
        while [ -z "$val" ]; do
            read -r -p "$prompt_text: " val
        done
    fi
    eval "$var_name=\"$val\""
}

# 1. Read existing .env or prompt interactively
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    echo -e "${INFO} Загрузка конфигурации из существующего .env..."
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

VPN_DOMAIN="${VPN_DOMAIN:-}"
TG_DOMAIN="${TG_DOMAIN:-}"
ACME_EMAIL="${ACME_EMAIL:-}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_ADMIN_IDS="${TELEGRAM_ADMIN_IDS:-}"
CASCADE_DOMAIN="${CASCADE_DOMAIN:-}"

if [ -z "$VPN_DOMAIN" ]; then
    echo -e "${BOLD}Настройка доменных имён и безопасности:${NC}"
    prompt_var VPN_DOMAIN "Основной домен для NaiveProxy и сайта (например: edge.mydomain.com)"
    prompt_var TG_DOMAIN "Поддомен для Telegram Web Proxy (например: tg.mydomain.com)" "tg.${VPN_DOMAIN#edge.}"
    prompt_var ACME_EMAIL "Email для SSL-сертификатов Let's Encrypt" "admin@${VPN_DOMAIN#edge.}"
    prompt_var TELEGRAM_BOT_TOKEN "Токен Telegram-бота (от @BotFather)"
    prompt_var TELEGRAM_ADMIN_IDS "Ваш цифровой Telegram ID (от @userinfobot)"
    prompt_var CASCADE_DOMAIN "Домен или IP российского каскадного сервера (опционально, Enter для пропуска)" ""
fi

# 2. Disable IPv6 & install system packages
echo -e "\n${INFO} Отключение IPv6 по умолчанию (защита от утечек DNS и асимметричной маршрутизации)..."
cat << 'EOF' > /etc/sysctl.d/99-disable-ipv6.conf
# Disable IPv6 system-wide (Prevents IP/DNS leaks, dual-stack routing & Let's Encrypt AAAA mismatch)
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.lo.disable_ipv6=1 >/dev/null 2>&1 || true

if [ -f /etc/default/ufw ]; then
    sed -i 's/^IPV6=yes/IPV6=no/' /etc/default/ufw 2>/dev/null || true
fi

echo -e "${INFO} Установка системных пакетов и зависимостей..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    curl git jq ufw fail2ban python3 python3-venv python3-pip \
    libcap2-bin docker.io

systemctl enable --now docker

# 3. Configure Firewall and System Hardening
echo -e "${INFO} Настройка фаервола UFW и защиты ядра..."
bash "$SCRIPT_DIR/scripts/setup-firewall.sh"
bash "$SCRIPT_DIR/scripts/setup-security.sh"

# 4. Build Caddy with NaiveProxy plugin
echo -e "${INFO} Сборка Caddy v2 с плагином NaiveProxy..."
bash "$SCRIPT_DIR/caddy/build-caddy.sh"

# 5. Prepare Web Directories & Stealth Showcase Site
echo -e "${INFO} Развёртывание витрины и сайта-маскировки..."
mkdir -p /var/www/site /etc/caddy
cp "$SCRIPT_DIR/site/index.html" /var/www/site/index.html

# 6. Generate Initial Cryptographic Secrets
PROBE_SECRET=$(openssl rand -hex 12)
TG_PROXY_SECRET=$(openssl rand -hex 16)
INITIAL_USER="user_$(openssl rand -hex 3)"
INITIAL_PASS=$(openssl rand -hex 10)

echo -e "${INFO} Генерация конфигурации /etc/caddy/Caddyfile..."
sed -e "s|{{ACME_EMAIL}}|$ACME_EMAIL|g" \
    -e "s|{{VPN_DOMAIN}}|$VPN_DOMAIN|g" \
    -e "s|{{TG_DOMAIN}}|$TG_DOMAIN|g" \
    -e "s|{{DEFAULT_USER}}|$INITIAL_USER|g" \
    -e "s|{{DEFAULT_PASS}}|$INITIAL_PASS|g" \
    -e "s|{{PROBE_SECRET}}|$PROBE_SECRET|g" \
    "$SCRIPT_DIR/caddy/Caddyfile.template" > /etc/caddy/Caddyfile

# Create system caddy user if not exists
if ! id -u caddy >/dev/null 2>&1; then
    useradd -r -s /bin/false -d /var/lib/caddy -m caddy
fi

chown -R caddy:caddy /var/www/site /etc/caddy
chmod 755 /var/www/site
chmod 644 /etc/caddy/Caddyfile

# 7. Install and Start Caddy Systemd Service
echo -e "${INFO} Запуск службы Caddy..."
cp "$SCRIPT_DIR/caddy/caddy.service" /etc/systemd/system/caddy.service
systemctl daemon-reload
systemctl enable --now caddy
systemctl restart caddy

# 8. Start Docker Telegram Web Proxy Container
echo -e "${INFO} Запуск официального контейнера Telegram Web Proxy..."
docker stop tgweb-proxy >/dev/null 2>&1 || true
docker rm tgweb-proxy >/dev/null 2>&1 || true

docker run -d \
    --name tgweb-proxy \
    --restart unless-stopped \
    -p "127.0.0.1:8080:80" \
    -e "TPROXY_HOSTNAME=$TG_DOMAIN" \
    -e "TPROXY_SECRET=$TG_PROXY_SECRET" \
    --security-opt no-new-privileges:true \
    --memory 128m \
    --cpus 0.5 \
    grayhoax/telegram-web-proxy:sha-959b1b5@sha256:5bb3a55cdee2959482f69e24e4c202d52b378968d85e18cd645c2095739ecb45

# 9. Setup Privileged Helper for Unprivileged Bot
echo -e "${INFO} Настройка изолированных привилегий управления..."
install -m 755 "$SCRIPT_DIR/bot/caddy-control.sh" /usr/local/bin/caddy-control

if ! id -u proxy-bot >/dev/null 2>&1; then
    useradd -r -s /bin/false -d /opt/proxy-bot -m proxy-bot
fi

cat << 'EOF' > /etc/sudoers.d/proxy-bot
# Restricted privileges for proxy-bot
proxy-bot ALL=(ALL) NOPASSWD: /usr/local/bin/caddy-control
EOF
chmod 440 /etc/sudoers.d/proxy-bot

# 10. Deploy Telegram Bot
echo -e "${INFO} Настройка и запуск Telegram-бота..."
mkdir -p /opt/proxy-bot
cp "$SCRIPT_DIR/bot/bot.py" \
   "$SCRIPT_DIR/bot/auth.py" \
   "$SCRIPT_DIR/bot/caddy_manager.py" \
   /opt/proxy-bot/

python3 -m venv /opt/proxy-bot/venv
/opt/proxy-bot/venv/bin/pip install --upgrade pip >/dev/null 2>&1
/opt/proxy-bot/venv/bin/pip install -r "$SCRIPT_DIR/bot/requirements.txt" >/dev/null 2>&1

cat <<EOF > /opt/proxy-bot/.env
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_ADMIN_IDS=${TELEGRAM_ADMIN_IDS}
VPN_DOMAIN=${VPN_DOMAIN}
TG_DOMAIN=${TG_DOMAIN}
TG_PROXY_SECRET=${TG_PROXY_SECRET}
CASCADE_DOMAIN=${CASCADE_DOMAIN}
CADDYFILE=/etc/caddy/Caddyfile
EOF

chown -R proxy-bot:proxy-bot /opt/proxy-bot
chown root:proxy-bot /opt/proxy-bot/.env
chmod 640 /opt/proxy-bot/.env
chown -R caddy:proxy-bot /etc/caddy
chmod 775 /etc/caddy
chmod 664 /etc/caddy/Caddyfile

cp "$SCRIPT_DIR/bot/caddy-bot.service" /etc/systemd/system/caddy-bot.service
systemctl daemon-reload
systemctl enable --now caddy-bot
systemctl restart caddy-bot

# Save local .env for updates
cat <<EOF > "$ENV_FILE"
VPN_DOMAIN=${VPN_DOMAIN}
TG_DOMAIN=${TG_DOMAIN}
ACME_EMAIL=${ACME_EMAIL}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_ADMIN_IDS=${TELEGRAM_ADMIN_IDS}
CASCADE_DOMAIN=${CASCADE_DOMAIN}
EOF
chmod 600 "$ENV_FILE"

NAIVE_LINK="https://${INITIAL_USER}:${INITIAL_PASS}@${VPN_DOMAIN}:443#🛡️ [Naive] Stealth Proxy"
TG_PROXY_LINK="https://t.me/webproxy?server=${TG_DOMAIN}&port=443&secret=${TG_PROXY_SECRET}"

echo ""
echo -e "${BOLD}═════════════════════════════════════════════════════════════════════${NC}"
echo -e "  🎉 ${OK} Развёртывание успешно завершено!"
echo -e "${BOLD}═════════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "🌐 ${BOLD}Сайт-маскировка & NaiveProxy:${NC} https://${VPN_DOMAIN}"
echo -e "✈️ ${BOLD}Telegram Web Proxy:${NC}           https://${TG_DOMAIN}"
echo ""
echo -e "🔗 ${BOLD}Ссылка для Shadowrocket (NaiveProxy):${NC}"
echo -e "   ${NAIVE_LINK}"
echo ""
echo -e "🚀 ${BOLD}Ссылка для Telegram Web Proxy (в 1 клик):${NC}"
echo -e "   ${TG_PROXY_LINK}"
echo ""
echo -e "🤖 ${BOLD}Telegram-бот:${NC} запущен и готов к выдаче ключей и управлению!"
echo -e "${BOLD}═════════════════════════════════════════════════════════════════════${NC}"
