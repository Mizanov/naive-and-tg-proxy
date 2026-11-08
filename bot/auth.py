# ==============================================================================
# bot/auth.py — Cryptographic Admin Authorization (Fail-Closed)
# ==============================================================================
import os
import logging
from functools import wraps
from typing import Optional, Set, Callable, Any, Union

logger = logging.getLogger("ProxyBot.Auth")


class AuthorizationConfigurationError(Exception):
    """Raised when authorization configuration is invalid, missing, or empty."""
    pass


def parse_admin_ids(raw_admin_ids: Optional[str]) -> Set[int]:
    """
    Parse and strictly validate TELEGRAM_ADMIN_IDS environment variable.

    Rules:
    - Must not be None, empty, or whitespace-only.
    - Must be a comma-separated list of positive integers.
    - If any entry is not a valid integer, raises AuthorizationConfigurationError.
    - Fail-closed: Returns a non-empty set of integer IDs on success, raises on any anomaly.
    """
    if raw_admin_ids is None or not str(raw_admin_ids).strip():
        raise AuthorizationConfigurationError(
            "CRITICAL: TELEGRAM_ADMIN_IDS is empty or not set. "
            "Refusing to start: fail-closed security requires at least one designated administrator ID."
        )

    admin_ids: Set[int] = set()
    parts = [p.strip() for p in str(raw_admin_ids).split(",") if p.strip()]

    if not parts:
        raise AuthorizationConfigurationError(
            "CRITICAL: No administrator IDs found in TELEGRAM_ADMIN_IDS."
        )

    for part in parts:
        if not part.isdigit() or int(part) <= 0:
            raise AuthorizationConfigurationError(
                f"CRITICAL: Invalid Telegram user ID '{part}' in TELEGRAM_ADMIN_IDS. "
                f"All IDs must be positive integers."
            )
        admin_ids.add(int(part))

    return admin_ids


def is_admin(user_id: Optional[int], admin_ids: Set[int]) -> bool:
    """Check if user_id is in admin_ids using strict fail-closed logic."""
    if not admin_ids or user_id is None:
        return False
    return user_id in admin_ids


def _make_auth_wrapper(func: Callable, explicit_admins: Optional[Set[int]]) -> Callable:
    @wraps(func)
    async def wrapper(update: Any, context: Any, *args: Any, **kwargs: Any) -> Any:
        target_admins = explicit_admins
        if target_admins is None:
            try:
                target_admins = parse_admin_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
            except Exception:
                target_admins = set()

        effective_user = getattr(update, "effective_user", None)
        user_id = getattr(effective_user, "id", None) if effective_user else None
        username = getattr(effective_user, "username", "none") if effective_user else "none"

        if not is_admin(user_id, target_admins):
            handler_name = getattr(func, "__name__", repr(func))
            logger.warning(
                "SECURITY: Unauthorized access blocked. handler=%s, user_id=%s, username=@%s",
                handler_name,
                user_id,
                username
            )

            callback_query = getattr(update, "callback_query", None)
            if callback_query:
                try:
                    await callback_query.answer("⛔ Доступ запрещён.", show_alert=True)
                except Exception as e:
                    logger.error("Failed to answer unauthorized callback query: %s", e)
                return None

            effective_message = getattr(update, "effective_message", None)
            if effective_message:
                try:
                    await effective_message.reply_text(
                        "⛔ **Доступ запрещен.**\n\nВаш Telegram ID не зарегистрирован в списке администраторов системы.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error("Failed to send unauthorized message reply: %s", e)
                return None

            return None

        return await func(update, context, *args, **kwargs)

    return wrapper


def admin_required(arg: Optional[Union[Callable, Set[int]]] = None) -> Callable:
    """
    Dual-mode decorator:
    Can be used as `@admin_required` (reading env dynamically)
    or as `@admin_required(admin_ids_set)`.
    """
    if callable(arg):
        return _make_auth_wrapper(arg, None)

    def decorator(func: Callable) -> Callable:
        return _make_auth_wrapper(func, arg)

    return decorator
