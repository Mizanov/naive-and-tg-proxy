# ==============================================================================
# bot/bot.py — Telegram Bot for NaiveProxy, TG Web Proxy & Smart Cascade
# ==============================================================================
import os
import re
import io
import json
import logging
import subprocess
from typing import Optional

import qrcode
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from auth import admin_required
from caddy_manager import (
    validate_username,
    list_users,
    add_naive_user,
    delete_naive_user,
    UserAlreadyExistsError,
    UserNotFoundError,
    InvalidUsernameError,
    CaddyManagerError,
    default_control_runner
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("ProxyBot")

# Environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
VPN_DOMAIN = os.getenv("VPN_DOMAIN", "vpn.example.com").strip()
TG_DOMAIN = os.getenv("TG_DOMAIN", "tg.example.com").strip()
TG_PROXY_SECRET = os.getenv("TG_PROXY_SECRET", "").strip()
CASCADE_DOMAIN = os.getenv("CASCADE_DOMAIN", "").strip()
CADDYFILE = os.getenv("CADDYFILE", "/etc/caddy/Caddyfile").strip()

if not TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Bot will not function until configured.")


def get_keyboard():
    keyboard = [
        ["➕ Создать NaiveProxy", "👥 Список пользователей"],
        ["🚀 Telegram Web Proxy", "🇷🇺 Каскадный шлюз"],
        ["📊 Статус сервера"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def generate_qr(text: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


@admin_required
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🛡️ **Stealth Proxy & Telegram Web Proxy Hub**\n\n"
        "Центр управления скрытым сетевым хабом на базе **Caddy v2** и **Docker**:\n\n"
        "• 🥷 **NaiveProxy (HTTP/2 CONNECT)**: 100% браузерная маскировка трафика через движок Chromium.\n"
        "• ✈️ **Telegram Web Proxy**: официальный MTProxy-over-HTTPS для мгновенного подключения Telegram в 1 клик.\n"
        "• 🇷🇺 **Каскадный шлюз**: умный сплит-роутинг через сервер в РФ (Госуслуги/Банки напрямую, остальное в туннель).\n\n"
        "Используйте кнопки меню ниже для управления:"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_keyboard())


@admin_required
async def new_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ℹ️ Укажите имя пользователя:\n`/new <username>`", parse_mode="Markdown")
        return
    await process_new_user(update, context.args[0].strip())


async def process_new_user(update: Update, username: str):
    try:
        valid_user = validate_username(username)
    except InvalidUsernameError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    try:
        pwd = add_naive_user(valid_user, caddyfile_path=CADDYFILE)
    except UserAlreadyExistsError:
        await update.message.reply_text(f"⚠️ Пользователь `{valid_user}` уже существует!", parse_mode="Markdown")
        return
    except CaddyManagerError as e:
        logger.error("Failed to add user: %s", e)
        await update.message.reply_text(f"❌ Ошибка настройки Caddy: {e}")
        return

    naive_link = f"https://{valid_user}:{pwd}@{VPN_DOMAIN}:443#🛡️ [Naive] Stealth Proxy"
    client_config = {
        "listen": "socks://127.0.0.1:1080",
        "proxy": f"https://{valid_user}:{pwd}@{VPN_DOMAIN}"
    }
    client_json_str = json.dumps(client_config, indent=2)

    msg = (
        f"✅ **Ключ NaiveProxy успешно создан!**\n\n"
        f"👤 **Пользователь:** `{valid_user}`\n"
        f"🔑 **Пароль:** `{pwd}`\n\n"
        f"🔗 **Ссылка для Shadowrocket / NekoBox (HTTPS):**\n"
        f"`{naive_link}`\n\n"
        f"💻 **Конфигурация для ПК (config.json):**\n"
        f"```json\n{client_json_str}\n```"
    )

    qr_img = generate_qr(naive_link)
    keyboard = [
        [InlineKeyboardButton("🗑 Удалить ключ", callback_data=f"del_{valid_user}")]
    ]
    await update.message.reply_photo(
        photo=InputFile(qr_img, filename=f"{valid_user}_naive_qr.png"),
        caption=msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_required
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = list_users(caddyfile_path=CADDYFILE)
    if not users:
        await update.message.reply_text("ℹ️ Список пользователей NaiveProxy пуст.")
        return

    msg = f"👥 **Пользователи NaiveProxy ({len(users)}):**\n\n"
    keyboard = []
    for u in sorted(users.keys()):
        msg += f"• `{u}`\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Удалить {u}", callback_data=f"del_{u}")])

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_required
async def del_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ℹ️ Укажите имя пользователя:\n`/del <username>`", parse_mode="Markdown")
        return
    username = context.args[0].strip()
    try:
        delete_naive_user(username, caddyfile_path=CADDYFILE)
        await update.message.reply_text(f"✅ Пользователь `{username}` удален.", parse_mode="Markdown")
    except UserNotFoundError:
        await update.message.reply_text(f"⚠️ Пользователь `{username}` не найден.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка удаления: {e}")


@admin_required
async def proxy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    secret = TG_PROXY_SECRET
    if not secret:
        # Try reading from docker
        try:
            r = default_control_runner("tgproxy-link")
            match = re.search(r"secret=(\S+)", r.stdout)
            if match:
                secret = match.group(1)
        except Exception:
            secret = "tproxy_secret"

    tg_link = f"https://t.me/webproxy?server={TG_DOMAIN}&port=443&secret={secret}"
    msg = (
        "✈️ **Официальный Telegram Web Proxy**\n\n"
        "Маскировка MTProxy внутри HTTPS-сессий по протоколу WebSocket.\n\n"
        f"🖥 **Сервер:** `{TG_DOMAIN}`\n"
        f"🔐 **Секрет:** `{secret}`\n\n"
        f"🔗 **Ссылка подключения:**\n`{tg_link}`"
    )

    qr_img = generate_qr(tg_link)
    keyboard = [
        [InlineKeyboardButton("🚀 Подключить в Telegram", url=tg_link)]
    ]
    await update.message.reply_photo(
        photo=InputFile(qr_img, filename="tg_webproxy_qr.png"),
        caption=msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_required
async def cascade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = CASCADE_DOMAIN or "msk.example.ru"
    msg = (
        "🇷🇺 **Российский каскадный шлюз (Smart Cascade)**\n\n"
        "Схема маршрутизации:\n"
        "`Клиент ➔ Сервер Москва (Шлюз) ➔ Этот сервер (HostVDS Европа)`\n\n"
        "🌟 **Преимущества каскада:**\n"
        "• Российские сервисы (Сбер, Т-Банк, Госуслуги, Авито) открываются напрямую с московского IP (без капч и ограничений).\n"
        "• Telegram, YouTube, заблокированные сайты автоматически перенаправляются в зашифрованный туннель в Европу.\n"
        "• Для интернет-провайдеров в РФ подключение выглядит как внутренний трафик внутри страны.\n\n"
        f"📍 **Шлюз:** `{host}`\n"
        "Инструкция по развёртыванию шлюза на сервере в РФ: скрипт `cascade/setup-cascade.sh`."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


@admin_required
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    def get_caddy_status() -> str:
        try:
            r = default_control_runner("caddy-status")
            return "🟢 Active" if "active" in r.stdout.lower() else "🔴 Inactive"
        except Exception:
            return "🔴 Error"

    def get_docker_status() -> str:
        try:
            r = default_control_runner("docker-status", "tgweb-proxy")
            status = r.stdout.strip()
            return "🟢 Running" if status == "running" else f"🔴 {status}"
        except Exception:
            return "⚪ Unchecked"

    mem_r = subprocess.run(["free", "-m"], capture_output=True, text=True)
    df_r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)

    caddy_st = get_caddy_status()
    wp_st = get_docker_status()

    msg = (
        "📊 **Статус сервера:**\n\n"
        f"🌐 **Caddy (NaiveProxy + SSL):** {caddy_st}\n"
        f"✈️ **Telegram Web Proxy:** {wp_st}\n\n"
        f"💾 **Диск:**\n```{df_r.stdout.strip()}```\n\n"
        f"🧠 **Оперативная память:**\n```{mem_r.stdout.strip()}```"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


@admin_required
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("del_"):
        username = data[4:]
        try:
            delete_naive_user(username, caddyfile_path=CADDYFILE)
            await query.message.reply_text(f"✅ Пользователь `{username}` успешно удален.", parse_mode="Markdown")
        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка удаления: {e}")


@admin_required
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "➕ Создать NaiveProxy":
        await update.message.reply_text("ℹ️ Введите команду: `/new <имя_пользователя>`", parse_mode="Markdown")
    elif text == "👥 Список пользователей":
        await list_users_command(update, context)
    elif text == "🚀 Telegram Web Proxy":
        await proxy_command(update, context)
    elif text == "🇷🇺 Каскадный шлюз":
        await cascade_command(update, context)
    elif text == "📊 Статус сервера":
        await status_command(update, context)
    elif re.match(r"^[a-zA-Z0-9_-]+$", text) and not text.startswith("/"):
        await process_new_user(update, text)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", start_handler))
    app.add_handler(CommandHandler("new", new_user_command))
    app.add_handler(CommandHandler("add", new_user_command))
    app.add_handler(CommandHandler("list", list_users_command))
    app.add_handler(CommandHandler("users", list_users_command))
    app.add_handler(CommandHandler("del", del_user_command))
    app.add_handler(CommandHandler("proxy", proxy_command))
    app.add_handler(CommandHandler("webproxy", proxy_command))
    app.add_handler(CommandHandler("cascade", cascade_command))
    app.add_handler(CommandHandler("status", status_command))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("ProxyBot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
