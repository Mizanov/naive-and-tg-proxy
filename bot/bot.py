# ==============================================================================
# bot/bot.py — Telegram Bot for NaiveProxy, TG Web Proxy & Smart Cascade
# ==============================================================================
import os
import re
import io
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Optional, Set, Dict, Any

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

from auth import (
    admin_required,
    parse_admin_ids,
    is_admin as auth_is_admin,
    AuthorizationConfigurationError
)
from caddy_manager import (
    validate_username,
    list_users,
    add_naive_user,
    delete_naive_user,
    get_user_by_tg_id,
    get_all_users_meta,
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
NODE_DOMAIN = os.getenv("NODE_DOMAIN", os.getenv("VPN_DOMAIN", "node.example.com")).strip()
VPN_DOMAIN = NODE_DOMAIN
TG_DOMAIN = os.getenv("TG_DOMAIN", "tg.example.com").strip()
TG_PROXY_SECRET = os.getenv("TG_PROXY_SECRET", "").strip()
CASCADE_DOMAIN = os.getenv("CASCADE_DOMAIN", "").strip()
CADDYFILE = os.getenv("CADDYFILE", "/etc/caddy/Caddyfile").strip()

if not TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Bot will not function until configured.")


def get_admin_ids() -> Set[int]:
    """Retrieves designated administrator IDs strictly."""
    try:
        return parse_admin_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
    except Exception:
        return set()


def is_admin_user(user_id: Optional[int]) -> bool:
    """Strict fail-closed admin check."""
    return auth_is_admin(user_id, get_admin_ids())


def get_admin_keyboard():
    keyboard = [
        ["➕ Создать NaiveProxy", "👥 Список участников"],
        ["🚀 Telegram Web Proxy", "🇷🇺 Каскадный шлюз"],
        ["📊 Статус сервера"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_user_unregistered_keyboard():
    keyboard = [
        ["🚀 Запросить доступ", "📖 Инструкция"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_user_registered_keyboard():
    keyboard = [
        ["📱 Мой NaiveProxy", "✈️ Telegram Proxy"],
        ["📖 Инструкция"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_keyboard():
    """Compatibility alias for admin keyboard."""
    return get_admin_keyboard()


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


def get_tg_proxy_info() -> Dict[str, str]:
    info = {
        "server": TG_DOMAIN,
        "secret": TG_PROXY_SECRET or "tproxy_secret",
        "link": f"https://t.me/webproxy?server={TG_DOMAIN}&port=443&secret={TG_PROXY_SECRET or 'tproxy_secret'}"
    }
    try:
        r = default_control_runner("tgproxy-link")
        link_m = re.search(r"Proxy link:\s+(https://\S+)", r.stdout)
        server_m = re.search(r"Proxy server \(enter manually\):\s+(\S+)", r.stdout)
        secret_m = re.search(r"Proxy secret \(enter manually\):\s+(\S+)", r.stdout)
        if link_m:
            info["link"] = link_m.group(1).strip()
        if server_m:
            info["server"] = server_m.group(1).strip()
        if secret_m:
            info["secret"] = secret_m.group(1).strip()
    except Exception as e:
        logger.error("Failed to query tgproxy-link: %s", e)
    return info


def get_tg_proxy_secret() -> str:
    return get_tg_proxy_info()["secret"]


def get_tg_proxy_link() -> str:
    return get_tg_proxy_info()["link"]


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    if is_admin_user(user.id):
        await send_admin_panel(update, context)
    else:
        await send_user_cabinet(update, context)


async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🛡️ **Stealth Proxy & Telegram Web Proxy Hub**\n\n"
        "Центр управления скрытым сетевым узлом на базе **Caddy v2** и **Docker**:\n\n"
        "• 🥷 **NaiveProxy (HTTP/2 CONNECT)**: 100% браузерная маскировка трафика через движок Chromium.\n"
        "• ✈️ **Telegram Web Proxy**: официальный MTProxy-over-HTTPS для мгновенного подключения Telegram в 1 клик.\n"
        "• 🇷🇺 **Каскадный шлюз**: умный сплит-роутинг через сервер в РФ (Госуслуги/Банки напрямую, остальное в туннель).\n\n"
        "Используйте кнопки меню ниже для управления:"
    )
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(msg, parse_mode="Markdown", reply_markup=get_admin_keyboard())


async def send_user_cabinet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = get_user_by_tg_id(user.id)
    target = update.callback_query.message if update.callback_query else update.message

    if existing:
        username, _ = existing
        msg = (
            f"👋 Здравствуйте, {user.first_name}!\n\n"
            f"Добро пожаловать в **Личный кабинет участника защищённого прокси-узла**.\n\n"
            f"👤 **Ваш профиль:** `{username}`\n"
            f"🟢 **Статус доступа:** Активен\n\n"
            f"Здесь вы можете получить параметры подключения NaiveProxy (Chromium HTTPS) "
            f"или мгновенно подключить официальный Telegram Web Proxy в 1 клик."
        )
        keyboard = [
            [InlineKeyboardButton("📱 Мой NaiveProxy", callback_data="my_naive")],
            [
                InlineKeyboardButton("✈️ Telegram Web Proxy", callback_data="my_tgproxy"),
                InlineKeyboardButton("📖 Инструкция", callback_data="user_guide")
            ]
        ]
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=get_user_registered_keyboard())
        await target.reply_text("Выберите необходимое действие:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        msg = (
            f"👋 Здравствуйте, {user.first_name}!\n\n"
            f"Добро пожаловать в **Персональный портал доступа к скрытому сетевому хабу**.\n\n"
            f"Хаб предоставляет персональный доступ к двум защищённым протоколам:\n"
            f"1️⃣ **NaiveProxy**: транспорт через стек Chromium (маскировка под легитимный просмотр веб-сайтов по HTTPS/2).\n"
            f"2️⃣ **Telegram Web Proxy**: официальный прокси для Telegram без сторонних приложений в 1 клик.\n\n"
            f"📌 **Правила хаба:**\n"
            f"• Доступ предоставляется участникам персонально (1 профиль на Telegram-аккаунт).\n"
            f"• Поддерживаются устройства на iOS, Android, Windows, macOS и Linux.\n\n"
            f"Нажмите кнопку ниже, чтобы получить персональный доступ:"
        )
        keyboard = [
            [InlineKeyboardButton("🚀 Получить доступ к шлюзу", callback_data="req_access")],
            [InlineKeyboardButton("📖 Инструкция по подключению", callback_data="user_guide")]
        ]
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=get_user_unregistered_keyboard())
        await target.reply_text("Нажмите для выпуска ключа:", reply_markup=InlineKeyboardMarkup(keyboard))


async def request_access_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    # Single-issue self-service lock
    existing = get_user_by_tg_id(user.id)
    if existing:
        username, _ = existing
        msg = f"⚠️ У вас уже активирован персональный доступ: `{username}`.\nПовторный выпуск ключа заблокирован."
        if update.callback_query:
            await update.callback_query.answer("Доступ уже предоставлен!", show_alert=True)
            await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_user_registered_keyboard())
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_user_registered_keyboard())
        return

    # Derive unique sanitized username
    base_name = ""
    if user.username:
        clean_user = re.sub(r"[^a-zA-Z0-9_-]", "", user.username.lower())
        if len(clean_user) >= 3:
            base_name = f"tg_{clean_user[:20]}"
    if not base_name:
        base_name = f"user_{user.id}"

    existing_users = set(list_users(caddyfile_path=CADDYFILE).keys())
    candidate_name = base_name
    counter = 1
    while candidate_name in existing_users:
        candidate_name = f"{base_name}_{counter}"
        counter += 1

    extra_meta = {
        "tg_id": user.id,
        "tg_username": user.username or "",
        "tg_first_name": user.first_name or "",
        "tg_last_name": user.last_name or "",
        "issued_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        pwd = add_naive_user(candidate_name, caddyfile_path=CADDYFILE, extra_meta=extra_meta)
    except Exception as e:
        logger.error("Failed to self-register user %s (%s): %s", user.id, candidate_name, e)
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(f"❌ Не удалось выпустить ключ: {e}. Обратитесь к администратору.")
        return

    if update.callback_query:
        await update.callback_query.answer("Доступ успешно активирован!")

    target_msg = update.callback_query.message if update.callback_query else update.message
    await deliver_credentials(target_msg, candidate_name, pwd, is_self_service=True)

    # Notify admins
    for admin_id in get_admin_ids():
        try:
            admin_notify = (
                "🔔 **Новый участник получил доступ к шлюзу:**\n\n"
                f"• Профиль: `{candidate_name}`\n"
                f"• Имя: {user.full_name}\n"
                f"• Telegram: @{user.username or '—'} (`{user.id}`)"
            )
            await context.bot.send_message(chat_id=admin_id, text=admin_notify, parse_mode="Markdown")
        except Exception as e:
            logger.debug("Failed to send admin notification to %s: %s", admin_id, e)


async def deliver_credentials(message_target, username: str, pwd: str, is_self_service: bool = False):
    naive_link = f"https://{username}:{pwd}@{VPN_DOMAIN}:443#🛡️ [Naive] Stealth Proxy"
    tg_link = get_tg_proxy_link()

    client_config = {
        "listen": "socks://127.0.0.1:1080",
        "proxy": f"https://{username}:{pwd}@{VPN_DOMAIN}"
    }
    client_json_str = json.dumps(client_config, indent=2)

    caption = (
        f"🎉 **Ваш персональный доступ успешно активирован!**\n\n"
        f"👤 **Пользователь:** `{username}`\n"
        f"🔑 **Пароль:** `{pwd}`\n\n"
        f"1️⃣ **NaiveProxy (Chromium HTTPS/2):**\n"
        f"• Ссылка для Shadowrocket / NekoBox / v2rayN:\n"
        f"`{naive_link}`\n\n"
        f"2️⃣ **Telegram Web Proxy (1 клик):**\n"
        f"`{tg_link}`\n\n"
        f"💻 **Конфиг для ПК (config.json):**\n"
        f"```json\n{client_json_str}\n```"
    )

    qr_img = generate_qr(naive_link)
    if is_self_service:
        keyboard = [
            [InlineKeyboardButton("✈️ Подключить Telegram Web Proxy", url=tg_link)],
            [InlineKeyboardButton("📖 Инструкция по подключению", callback_data="user_guide")]
        ]
        reply_kb = get_user_registered_keyboard()
    else:
        keyboard = [
            [InlineKeyboardButton("✈️ Подключить Telegram Web Proxy", url=tg_link)],
            [InlineKeyboardButton("🗑 Удалить ключ", callback_data=f"del_{username}")]
        ]
        reply_kb = get_admin_keyboard()

    try:
        await message_target.reply_photo(
            photo=InputFile(qr_img, filename=f"{username}_naive_qr.png"),
            caption=caption[:1024],
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as photo_err:
        logger.warning(f"Failed to send QR photo: {photo_err}, falling back to text message")
        await message_target.reply_text(
            caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

    await message_target.reply_text(
        f"⚡ **Прямая ссылка NaiveProxy для копирования:**\n`{naive_link}`",
        parse_mode="Markdown",
        reply_markup=reply_kb,
        disable_web_page_preview=True
    )


async def show_my_naive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    existing = get_user_by_tg_id(user.id)
    target = update.callback_query.message if update.callback_query else update.message

    if not existing:
        await send_user_cabinet(update, context)
        return

    username, _ = existing
    users = list_users(caddyfile_path=CADDYFILE)
    pwd = users.get(username)
    if not pwd:
        await target.reply_text(f"⚠️ Учётная запись `{username}` не найдена в Caddyfile. Обратитесь к администратору.")
        return

    naive_link = f"https://{username}:{pwd}@{VPN_DOMAIN}:443#🛡️ [Naive] Stealth Proxy"
    client_config = {
        "listen": "socks://127.0.0.1:1080",
        "proxy": f"https://{username}:{pwd}@{VPN_DOMAIN}"
    }
    client_json_str = json.dumps(client_config, indent=2)

    msg = (
        f"📱 **Параметры NaiveProxy**\n\n"
        f"👤 **Пользователь:** `{username}`\n"
        f"🔑 **Пароль:** `{pwd}`\n\n"
        f"🔗 **Ссылка подключения (NekoBox / Shadowrocket):**\n"
        f"`{naive_link}`\n\n"
        f"💻 **Конфиг для ПК (config.json):**\n"
        f"```json\n{client_json_str}\n```"
    )

    qr_img = generate_qr(naive_link)
    keyboard = [
        [InlineKeyboardButton("📖 Инструкция", callback_data="user_guide")]
    ]
    if update.callback_query:
        await update.callback_query.answer()

    try:
        await target.reply_photo(
            photo=InputFile(qr_img, filename=f"{username}_qr.png"),
            caption=msg[:1024],
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_my_tgproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = get_tg_proxy_info()
    tg_link = info["link"]
    srv = info["server"]
    sec = info["secret"]
    msg = (
        "✈️ **Официальный Telegram Web Proxy**\n\n"
        "Маскировка MTProxy внутри HTTPS-сессий по протоколу WebSocket.\n\n"
        f"🖥 **Сервер:** `{srv}`\n"
        f"🔐 **Секрет:** `{sec}`\n\n"
        f"🔗 **Ссылка подключения:**\n`{tg_link}`"
    )

    qr_img = generate_qr(tg_link)
    keyboard = [
        [InlineKeyboardButton("🚀 Подключить в Telegram", url=tg_link)]
    ]
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    await target.reply_photo(
        photo=InputFile(qr_img, filename="tg_webproxy_qr.png"),
        caption=msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_user_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 **Инструкция по подключению к скрытому хабу**\n\n"
        "1️⃣ **Telegram Web Proxy (самый простой способ):**\n"
        "• Нажмите кнопку «✈️ Telegram Proxy» и кликните «🚀 Подключить в Telegram».\n"
        "• Telegram подключится напрямую через зашифрованный HTTPS-канал.\n\n"
        "2️⃣ **NaiveProxy (полный браузерный транспорт):**\n"
        "• **iOS**: установите приложение **Shadowrocket** из App Store. Отсканируйте QR-код или вставьте ссылку из раздела «📱 Мой NaiveProxy».\n"
        "• **Android**: установите **NekoBox** (из GitHub/Play Store), нажмите «+» ➔ «Импорт из буфера» или отсканируйте QR-код.\n"
        "• **Windows / macOS / Linux**: используйте официальный бинарник `naive` с файлом `config.json`.\n\n"
        "💡 *При любых вопросах обращайтесь к администратору вашего узла.*"
    )
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()
    await target.reply_text(msg, parse_mode="Markdown")


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

    await deliver_credentials(update.message, valid_user, pwd, is_self_service=False)


@admin_required
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = list_users(caddyfile_path=CADDYFILE)
    if not users:
        await update.message.reply_text("ℹ️ Список пользователей NaiveProxy пуст.", reply_markup=get_admin_keyboard())
        return

    meta_registry = get_all_users_meta()

    msg = f"👥 **Пользователи NaiveProxy ({len(users)}):**\n\n"
    keyboard = []
    for u in sorted(users.keys()):
        meta = meta_registry.get(u, {})
        tg_info = ""
        if meta and isinstance(meta, dict):
            parts = []
            if meta.get("tg_username"):
                parts.append(f"@{meta['tg_username']}")
            if meta.get("tg_first_name"):
                parts.append(meta["tg_first_name"])
            if meta.get("tg_id"):
                parts.append(f"ID:{meta['tg_id']}")
            if parts:
                tg_info = f" ({', '.join(parts)})"

        msg += f"• `{u}`{tg_info}\n"
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
    await show_my_tgproxy(update, context)


@admin_required
async def cascade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = CASCADE_DOMAIN or "saltrss.duckdns.org"
    vless_uuid = os.getenv("CASCADE_VLESS_UUID", "c79b8760-1718-4e50-8d62-48bc61978193").strip()
    mixed_user = os.getenv("CASCADE_USER", "mizanov").strip()
    mixed_pass = os.getenv("CASCADE_PASS", "3eadbaae7070f8ac1dbb").strip()
    mixed_port = os.getenv("CASCADE_PORT", "10808").strip()

    vless_link = f"vless://{vless_uuid}@{host}:443?type=ws&security=tls&path=%2Fgateway-stream%2F#🇷🇺 [Smart Cascade] Moscow"
    socks_link = f"socks5://{mixed_user}:{mixed_pass}@{host}:{mixed_port}#🇷🇺 [Smart Cascade SOCKS5] Moscow"

    msg = (
        "🇷🇺 **Российский каскадный шлюз (Smart Cascade)**\n\n"
        "Схема маршрутизации:\n"
        "`Клиент ➔ Сервер Москва (Шлюз) ➔ Сервер Германия (Транзит)`\n\n"
        "🌟 **Преимущества каскада:**\n"
        "• Российские сервисы (Сбер, Т-Банк, Госуслуги, Авито, .ru) открываются напрямую с московского IP (0 задержек, без капч).\n"
        "• Международные платформы и API автоматически направляются в зашифрованный туннель в Европу.\n"
        "• Внешний трафик выглядит как легитимный HTTPS на порт 443.\n\n"
        "1️⃣ **Защищённый VLESS-WebSocket (Рекомендуется):**\n"
        f"`{vless_link}`\n\n"
        "2️⃣ **Смешанный прокси (Mixed SOCKS5 / HTTP):**\n"
        f"• Хост: `{host}` | Порт: `{mixed_port}`\n"
        f"• Логин: `{mixed_user}` | Пароль: `{mixed_pass}`\n"
        f"• Ссылка для импорта: `{socks_link}`"
    )

    qr_img = generate_qr(vless_link)
    target = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()

    await target.reply_photo(
        photo=InputFile(qr_img, filename="cascade_qr.png"),
        caption=msg,
        parse_mode="Markdown"
    )


@admin_required
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    def get_caddy_status() -> str:
        try:
            r = default_control_runner("caddy-status")
            return "🟢 Active" if "active" in r.stdout.lower() else "🔴 Inactive"
        except Exception as e:
            logger.error("Failed to check Caddy status: %s", e)
            return "🔴 Error"

    def get_docker_status() -> str:
        try:
            r = default_control_runner("docker-status", "tgweb-proxy")
            status = r.stdout.strip()
            return "🟢 Running" if status == "running" else f"🔴 {status}"
        except Exception as e:
            logger.error("Failed to check Docker status: %s", e)
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


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

    if data == "req_access":
        await request_access_handler(update, context)
        return
    elif data == "my_naive":
        await show_my_naive(update, context)
        return
    elif data == "my_tgproxy":
        await show_my_tgproxy(update, context)
        return
    elif data == "user_guide":
        await show_user_guide(update, context)
        return

    # Admin actions
    if not is_admin_user(user.id if user else None):
        await query.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    if data.startswith("del_"):
        username = data[4:]
        try:
            delete_naive_user(username, caddyfile_path=CADDYFILE)
            await query.message.reply_text(f"✅ Пользователь `{username}` успешно удален.", parse_mode="Markdown")
        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка удаления: {e}")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    text = update.message.text.strip()

    if is_admin_user(user.id):
        if text == "➕ Создать NaiveProxy":
            await update.message.reply_text("ℹ️ Введите команду: `/new <имя_пользователя>`", parse_mode="Markdown")
        elif text in ("👥 Список пользователей", "👥 Список участников"):
            await list_users_command(update, context)
        elif text == "🚀 Telegram Web Proxy":
            await proxy_command(update, context)
        elif text == "🇷🇺 Каскадный шлюз":
            await cascade_command(update, context)
        elif text == "📊 Статус сервера":
            await status_command(update, context)
        elif re.match(r"^[a-zA-Z0-9_-]+$", text) and not text.startswith("/"):
            await process_new_user(update, text)
        else:
            await update.message.reply_text("ℹ️ Используйте кнопки меню администратора:", reply_markup=get_admin_keyboard())
    else:
        if text in ("🚀 Запросить доступ", "🚀 Получить доступ"):
            await request_access_handler(update, context)
        elif text in ("📱 Мой NaiveProxy", "📱 Мой профиль"):
            await show_my_naive(update, context)
        elif text in ("✈️ Telegram Proxy", "✈️ Telegram Web Proxy"):
            await show_my_tgproxy(update, context)
        elif text in ("📖 Инструкция", "/help"):
            await show_user_guide(update, context)
        else:
            existing = get_user_by_tg_id(user.id)
            kb = get_user_registered_keyboard() if existing else get_user_unregistered_keyboard()
            await update.message.reply_text("Пожалуйста, воспользуйтесь кнопками меню:", reply_markup=kb)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", show_user_guide))
    app.add_handler(CommandHandler("cabinet", start_handler))
    app.add_handler(CommandHandler("get", request_access_handler))
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
