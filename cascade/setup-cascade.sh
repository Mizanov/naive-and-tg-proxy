#!/usr/bin/env bash
# ==============================================================================
# cascade/setup-cascade.sh — One-Click Smart Cascade Gateway for Russian VPS
# ==============================================================================
# Run this script on your Russian VPS (e.g., Moscow Rebrain/Timeweb/Selectel).
# It configures an intelligent split-routing gateway:
# - Russian banks, Gosuslugi, .ru sites -> Direct Outbound (Russian IP, 0 captcha)
# - Blocked / International traffic -> Encrypted HTTP/2 Tunnel to European Node
# ==============================================================================
set -euo pipefail

INFO="\033[1;34m[INFO]\033[0m"
OK="\033[1;32m[OK]\033[0m"
ERR="\033[1;31m[ERROR]\033[0m"

echo "═════════════════════════════════════════════════════════════════════"
echo "  🇷🇺 Развёртывание умного каскадного шлюза (Smart Cascade Gateway)   "
echo "═════════════════════════════════════════════════════════════════════"

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

prompt_var EUROPE_DOMAIN "Домен удалённого сервера с NaiveProxy (например, edge.mydomain.com)"
prompt_var NAIVE_USER "Имя пользователя NaiveProxy (из Telegram-бота)"
prompt_var NAIVE_PASS "Пароль NaiveProxy (из Telegram-бота)"
prompt_var LISTEN_PORT "Порт для подключения клиентов к локальному шлюзу (Mixed HTTP/SOCKS5)" "10808"

echo -e "\n${INFO} Установка зависимостей (curl, jq)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl jq

echo -e "${INFO} Установка легковесного ядра Sing-Box..."
if ! command -v sing-box >/dev/null 2>&1; then
    bash -c "$(curl -fsSL https://sing-box.app/deb/install.sh)" || {
        echo -e "${INFO} Резервная установка Sing-Box из бинарного релиза..."
        SB_VER="1.14.4"
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64) ARCH_NAME="amd64" ;;
            aarch64) ARCH_NAME="arm64" ;;
            *) echo -e "${ERR} Неподдерживаемая архитектура: $ARCH"; exit 1 ;;
        esac
        curl -fsSL "https://github.com/SagerNet/sing-box/releases/download/v${SB_VER}/sing-box-${SB_VER}-linux-${ARCH_NAME}.tar.gz" -o /tmp/sb.tar.gz
        tar -xzf /tmp/sb.tar.gz -C /tmp
        install -m 755 /tmp/sing-box-*/sing-box /usr/local/bin/sing-box
        rm -rf /tmp/sb*
    }
fi

RULESET_DIR="/var/lib/sing-box/rulesets"
mkdir -p "$RULESET_DIR" /etc/sing-box

echo -e "${INFO} Загрузка оптимизированных бинарных правил маршрутизации (.srs)..."
download_srs() {
    local name="$1"
    local url="https://raw.githubusercontent.com/SagerNet/sing-ruleset/rule-set-format/${name}"
    curl -fsSL --retry 3 --connect-timeout 5 "$url" -o "$RULESET_DIR/${name}" 2>/dev/null || true
}

download_srs "geoip-ru.srs"
download_srs "geosite-category-gov-ru.srs"
download_srs "geosite-category-ads-all.srs"
download_srs "geosite-yandex.srs"
download_srs "geosite-vk.srs"
download_srs "geosite-mailru.srs"

echo -e "${INFO} Генерация конфигурации умного сплит-роутинга..."
cat <<EOF > /etc/sing-box/config.json
{
  "log": {
    "level": "warn",
    "timestamp": true
  },
  "dns": {
    "servers": [
      {
        "type": "local",
        "tag": "dns-local"
      },
      {
        "type": "https",
        "tag": "dns-remote",
        "server": "https://1.1.1.1/dns-query"
      }
    ],
    "rules": [
      {
        "rule_set": [
          "geoip-ru",
          "category-gov-ru",
          "yandex",
          "vk",
          "mailru"
        ],
        "server": "dns-local"
      }
    ]
  },
  "inbounds": [
    {
      "type": "mixed",
      "tag": "mixed-in",
      "listen": "0.0.0.0",
      "listen_port": ${LISTEN_PORT},
      "sniff": true,
      "sniff_override_destination": true
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    },
    {
      "type": "http",
      "tag": "naive-europe",
      "server": "${EUROPE_DOMAIN}",
      "server_port": 443,
      "username": "${NAIVE_USER}",
      "password": "${NAIVE_PASS}",
      "tls": {
        "enabled": true,
        "server_name": "${EUROPE_DOMAIN}"
      }
    },
    {
      "type": "block",
      "tag": "block"
    }
  ],
  "route": {
    "rule_set": [
      {
        "type": "local",
        "tag": "category-ads-all",
        "format": "binary",
        "path": "${RULESET_DIR}/geosite-category-ads-all.srs"
      },
      {
        "type": "local",
        "tag": "category-gov-ru",
        "format": "binary",
        "path": "${RULESET_DIR}/geosite-category-gov-ru.srs"
      },
      {
        "type": "local",
        "tag": "geoip-ru",
        "format": "binary",
        "path": "${RULESET_DIR}/geoip-ru.srs"
      },
      {
        "type": "local",
        "tag": "yandex",
        "format": "binary",
        "path": "${RULESET_DIR}/geosite-yandex.srs"
      },
      {
        "type": "local",
        "tag": "vk",
        "format": "binary",
        "path": "${RULESET_DIR}/geosite-vk.srs"
      },
      {
        "type": "local",
        "tag": "mailru",
        "format": "binary",
        "path": "${RULESET_DIR}/geosite-mailru.srs"
      }
    ],
    "rules": [
      {
        "rule_set": ["category-ads-all"],
        "outbound": "block"
      },
      {
        "rule_set": [
          "geoip-ru",
          "category-gov-ru",
          "yandex",
          "vk",
          "mailru"
        ],
        "outbound": "direct"
      },
      {
        "domain_suffix": [
          ".ru",
          ".su",
          ".рф",
          "sberbank.ru",
          "tbank.ru",
          "tinkoff.ru",
          "gosuslugi.ru",
          "avito.ru",
          "kinopoisk.ru",
          "ozon.ru",
          "wildberries.ru"
        ],
        "outbound": "direct"
      }
    ],
    "final": "naive-europe",
    "auto_detect_interface": true
  }
}
EOF

echo -e "${INFO} Проверка валидности конфигурации Sing-Box..."
sing-box check -c /etc/sing-box/config.json

echo -e "${INFO} Настройка systemd-сервиса Sing-Box..."
cat << 'EOF' > /etc/systemd/system/sing-box.service
[Unit]
Description=Sing-Box Smart Cascade Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config.json
Restart=on-failure
RestartSec=5s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now sing-box.service
systemctl restart sing-box.service

RU_SERVER_IP=$(curl -s4 https://api.ipify.org || curl -s4 https://ifconfig.me || echo "IP_ВАШЕГО_СЕРВЕРА_РФ")

echo "═════════════════════════════════════════════════════════════════════"
echo -e "  🎉 ${OK} Каскадный шлюз успешно настроен и запущен!"
echo "═════════════════════════════════════════════════════════════════════"
echo "Параметры подключения клиентов в Shadowrocket / NekoBox / v2rayN:"
echo "  • Тип: SOCKS5 или HTTP"
echo "  • Сервер: ${RU_SERVER_IP}"
echo "  • Порт: ${LISTEN_PORT}"
echo ""
echo "Маршрутизация:"
echo "  🇷🇺 Сбер, Госуслуги, Т-Банк, Авито, .ru -> Напрямую через локальный шлюз"
echo "  🌐 Внешние облачные ресурсы, международные сервисы и API -> Защищённый туннель"
echo "═════════════════════════════════════════════════════════════════════"
