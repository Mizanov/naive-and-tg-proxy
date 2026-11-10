#!/usr/bin/env bash
# ==============================================================================
# update.sh — Safe Rolling Update for NaiveProxy & Telegram Web Proxy Server
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFO="\033[1;34m[INFO]\033[0m"
OK="\033[1;32m[OK]\033[0m"
WARN="\033[1;33m[WARN]\033[0m"
ERR="\033[1;31m[ERROR]\033[0m"

echo "═════════════════════════════════════════════════════════════════════"
echo "  🔄 Обновление NaiveProxy & Telegram Web Proxy Server               "
echo "═════════════════════════════════════════════════════════════════════"

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${ERR} Этот скрипт должен быть запущен с правами root!"
    exit 1
fi

echo -e "${INFO} Обновление сайта-маскировки..."
mkdir -p /var/www/site
cp "$SCRIPT_DIR/site/index.html" /var/www/site/index.html
chown -R caddy:caddy /var/www/site

echo -e "${INFO} Обновление привилегированного хелпера..."
install -m 755 "$SCRIPT_DIR/bot/caddy-control.sh" /usr/local/bin/caddy-control

echo -e "${INFO} Обновление кода Telegram-бота и прав доступа..."
cp "$SCRIPT_DIR/bot/bot.py" \
   "$SCRIPT_DIR/bot/auth.py" \
   "$SCRIPT_DIR/bot/caddy_manager.py" \
   /opt/proxy-bot/

usermod -aG docker proxy-bot 2>/dev/null || true
chown -R proxy-bot:proxy-bot /opt/proxy-bot
if [ -f /opt/proxy-bot/.env ]; then
    chown root:proxy-bot /opt/proxy-bot/.env
    chmod 640 /opt/proxy-bot/.env
fi
chown -R caddy:proxy-bot /etc/caddy
chmod 775 /etc/caddy
chmod 664 /etc/caddy/Caddyfile* 2>/dev/null || true

if [ -f /opt/proxy-bot/venv/bin/pip ]; then
    echo -e "${INFO} Проверка зависимостей Python..."
    /opt/proxy-bot/venv/bin/pip install --upgrade pip >/dev/null 2>&1 || true
    /opt/proxy-bot/venv/bin/pip install -r "$SCRIPT_DIR/bot/requirements.txt" >/dev/null 2>&1 || true
fi

echo -e "${INFO} Обновление службы caddy-bot и перезапуск Telegram-бота..."
cp "$SCRIPT_DIR/bot/caddy-bot.service" /etc/systemd/system/caddy-bot.service
if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify /etc/systemd/system/caddy-bot.service >/dev/null 2>&1 || true
fi
systemctl daemon-reload
systemctl restart caddy-bot

echo -e "${INFO} Проверка состояния Caddy..."
if command -v caddy >/dev/null 2>&1; then
    caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile >/dev/null 2>&1 && systemctl reload caddy || systemctl restart caddy
fi

echo -e "\n═════════════════════════════════════════════════════════════════════"
echo -e "  🎉 ${OK} Обновление успешно завершено!"
echo "═════════════════════════════════════════════════════════════════════"
echo "Статус служб:"
echo "  • Caddy:        $(systemctl is-active caddy 2>/dev/null || echo 'inactive')"
echo "  • Telegram Bot: $(systemctl is-active caddy-bot 2>/dev/null || echo 'inactive')"
echo "  • Docker Proxy: $(docker inspect -f '{{.State.Status}}' tgweb-proxy 2>/dev/null || echo 'inactive')"
echo "═════════════════════════════════════════════════════════════════════"
