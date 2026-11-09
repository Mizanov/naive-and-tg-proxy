#!/usr/bin/env bash
# ==============================================================================
# cascade/setup-cascade.sh — One-Click Smart Cascade Gateway for Russian VPS
# ==============================================================================
# Run this script on your Russian VPS (e.g., Moscow / LandVPS / Timeweb / Selectel).
# It configures an intelligent split-routing gateway:
# - Russian banks, Gosuslugi, .ru sites -> Direct Outbound (Russian IP, 0 captcha)
# - Global / International traffic -> Encrypted Chromium-grade HTTP/2 Tunnel to European Node
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

prompt_var EUROPE_DOMAIN "Домен удалённого европейского узла с NaiveProxy" "cascadevps.duckdns.org"
prompt_var NAIVE_USER "Имя пользователя NaiveProxy (из Telegram-бота)" "user_46ec57"
prompt_var NAIVE_PASS "Пароль NaiveProxy (из Telegram-бота)" "6e9e82891daf2efc44e9"
prompt_var MIXED_USER "Имя пользователя для шлюза (Mixed SOCKS5/HTTP)" "mizanov"
prompt_var MIXED_PASS "Пароль для шлюза (Mixed SOCKS5/HTTP)" "3eadbaae7070f8ac1dbb"
prompt_var VLESS_UUID "UUID для скрытого VLESS-WebSocket шлюза" "c79b8760-1718-4e50-8d62-48bc61978193"
prompt_var LISTEN_PORT "Порт для подключения клиентов (Mixed SOCKS5/HTTP)" "10808"

echo -e "\n${INFO} Установка базовых зависимостей (curl, jq, xz-utils)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl jq xz-utils

echo -e "${INFO} Установка официального NaiveProxy клиента (Chromium Transport)..."
if ! command -v naive >/dev/null 2>&1; then
    NAIVE_VER="154.0.8037.49-5"
    curl -fsSL "https://github.com/klzgrad/naiveproxy/releases/download/v${NAIVE_VER}/naiveproxy-v${NAIVE_VER}-linux-x64.tar.xz" -o /tmp/naive.tar.xz
    tar -xJf /tmp/naive.tar.xz -C /tmp
    install -m 755 /tmp/naiveproxy-*/naive /usr/local/bin/naive
    rm -rf /tmp/naive*
fi

echo -e "${INFO} Установка высокопроизводительного ядра маршрутизации Sing-Box..."
if ! command -v sing-box >/dev/null 2>&1; then
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
fi

echo -e "${INFO} Настройка транзитного туннеля NaiveProxy (naive-cascade.service)..."
mkdir -p /etc/naive
cat <<EOF > /etc/naive/config.json
{
  "listen": "socks://127.0.0.1:1080",
  "proxy": "https://${NAIVE_USER}:${NAIVE_PASS}@${EUROPE_DOMAIN}"
}
EOF
chmod 600 /etc/naive/config.json

cat << 'EOF' > /etc/systemd/system/naive-cascade.service
[Unit]
Description=NaiveProxy Smart Cascade Upstream Tunnel
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/naive /etc/naive/config.json
Restart=always
RestartSec=5s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now naive-cascade.service
systemctl restart naive-cascade.service

echo -e "${INFO} Загрузка оптимизированных бинарных правил маршрутизации (.srs)..."
RULESET_DIR="/var/lib/sing-box/rulesets"
mkdir -p "$RULESET_DIR" /etc/sing-box

download_geoip() {
    local name="$1"
    local url="https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/${name}"
    curl -fsSL --retry 3 --connect-timeout 5 "$url" -o "$RULESET_DIR/${name}" 2>/dev/null || true
}

download_geosite() {
    local name="$1"
    local url="https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/${name}"
    curl -fsSL --retry 3 --connect-timeout 5 "$url" -o "$RULESET_DIR/${name}" 2>/dev/null || true
}

download_geoip "geoip-ru.srs"
download_geosite "geosite-category-gov-ru.srs"
download_geosite "geosite-category-ads-all.srs"
download_geosite "geosite-yandex.srs"
download_geosite "geosite-vk.srs"
download_geosite "geosite-mailru.srs"

echo -e "${INFO} Генерация конфигурации умного сплит-роутинга Sing-Box..."
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
        "server": "1.1.1.1",
        "server_port": 443,
        "detour": "naive-europe"
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
          "ozon.ru",
          "wildberries.ru",
          "kinopoisk.ru"
        ],
        "server": "dns-local"
      }
    ],
    "final": "dns-remote",
    "strategy": "prefer_ipv4"
  },
  "inbounds": [
    {
      "type": "vless",
      "tag": "vless-in",
      "listen": "127.0.0.1",
      "listen_port": 10086,
      "users": [
        {
          "name": "${MIXED_USER}",
          "uuid": "${VLESS_UUID}"
        }
      ],
      "transport": {
        "type": "ws",
        "path": "/gateway-stream/"
      }
    },
    {
      "type": "mixed",
      "tag": "mixed-in",
      "listen": "0.0.0.0",
      "listen_port": ${LISTEN_PORT},
      "users": [
        {
          "username": "${MIXED_USER}",
          "password": "${MIXED_PASS}"
        }
      ]
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    },
    {
      "type": "socks",
      "tag": "naive-europe",
      "server": "127.0.0.1",
      "server_port": 1080
    },
    {
      "type": "block",
      "tag": "block"
    }
  ],
  "route": {
    "default_domain_resolver": "dns-local",
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
        "action": "sniff"
      },
      {
        "protocol": "dns",
        "action": "hijack-dns"
      },
      {
        "port": 53,
        "action": "hijack-dns"
      },
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
          "ozon.ru",
          "wildberries.ru",
          "kinopoisk.ru"
        ],
        "outbound": "direct"
      },
      {
        "network": "udp",
        "port": 443,
        "outbound": "block"
      },
      {
        "network": "udp",
        "outbound": "direct"
      }
    ],
    "final": "naive-europe",
    "auto_detect_interface": true
  }
}
EOF
chmod 600 /etc/sing-box/config.json

echo -e "${INFO} Проверка валидности конфигурации Sing-Box..."
sing-box check -c /etc/sing-box/config.json

echo -e "${INFO} Настройка systemd-сервиса Sing-Box..."
cat << 'EOF' > /etc/systemd/system/sing-box.service
[Unit]
Description=Sing-Box Smart Cascade Router
After=network.target network-online.target naive-cascade.service
Wants=network-online.target
Requires=naive-cascade.service

[Service]
Type=simple
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config.json
Restart=always
RestartSec=5s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

if command -v ufw >/dev/null 2>&1; then
    ufw allow ${LISTEN_PORT}/tcp comment 'Smart Cascade Mixed Proxy' >/dev/null 2>&1 || true
fi

systemctl daemon-reload
systemctl enable --now sing-box.service
systemctl restart sing-box.service

RU_SERVER_IP=$(curl -s4 https://api.ipify.org || curl -s4 https://ifconfig.me || echo "IP_ВАШЕГО_СЕРВЕРА_РФ")

echo "═════════════════════════════════════════════════════════════════════"
echo -e "  🎉 ${OK} Каскадный шлюз успешно настроен и запущен!"
echo "═════════════════════════════════════════════"
echo "1️⃣ Защищённое подключение VLESS-WS-TLS (порт 443 Nginx):"
echo "  vless://${VLESS_UUID}@${RU_SERVER_IP}:443?type=ws&security=tls&path=%2Fgateway-stream%2F#SmartCascade"
echo ""
echo "2️⃣ Прямое подключение Mixed SOCKS5/HTTP (порт ${LISTEN_PORT}):"
echo "  • Хост: ${RU_SERVER_IP}"
echo "  • Порт: ${LISTEN_PORT}"
echo "  • Логин: ${MIXED_USER}"
echo "  • Пароль: ${MIXED_PASS}"
echo ""
echo "Маршрутизация:"
echo "  🇷🇺 Сбер, Госуслуги, Т-Банк, Авито, .ru -> Напрямую через московский узел"
echo "  🌐 Международные сервисы и облачные платформы -> Защищённый туннель в Европу"
echo "═════════════════════════════════════════════════════════════════════"
