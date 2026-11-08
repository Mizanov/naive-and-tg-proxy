# ==============================================================================
# bot/caddy_manager.py — Transactional & Safe Caddyfile ForwardProxy Management
# ==============================================================================
import os
import re
import fcntl
import shutil
import logging
import secrets
import tempfile
import subprocess
from contextlib import contextmanager
from typing import Dict, List, Optional, Callable, Tuple

logger = logging.getLogger("ProxyBot")

DEFAULT_CADDYFILE = "/etc/caddy/Caddyfile"
DEFAULT_LOCK_FILE = "/tmp/caddy_config.lock"


class CaddyManagerError(Exception):
    """Base exception for Caddy manager errors."""
    pass


class InvalidUsernameError(CaddyManagerError):
    pass


class UserAlreadyExistsError(CaddyManagerError):
    pass


class UserNotFoundError(CaddyManagerError):
    pass


class CaddyValidationError(CaddyManagerError):
    pass


class CaddyReloadError(CaddyManagerError):
    pass


def validate_username(username: str) -> str:
    """Enforces strict alphanumeric username policy with safe symbols."""
    username = username.strip()
    if not re.match(r"^[a-zA-Z0-9_-]{3,32}$", username):
        raise InvalidUsernameError(
            f"Некорректное имя пользователя '{username}'. Допускаются только латинские буквы, "
            "цифры, дефис и подчёркивание (длина 3-32 символов)."
        )
    return username


def generate_secure_password(length: int = 16) -> str:
    """Generates cryptographically random credentials."""
    return secrets.token_hex(length // 2)


@contextmanager
def caddy_lock(lock_path: str = DEFAULT_LOCK_FILE):
    """File lock preventing concurrent mutating races."""
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def default_control_runner(*args) -> subprocess.CompletedProcess:
    """Default execution runner for system control actions."""
    cmd = []
    if os.geteuid() != 0:
        cmd.append("sudo")
    cmd.extend(["/usr/local/bin/caddy-control", *args])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=15,
        check=True
    )


def parse_naive_users(caddyfile_content: str) -> Dict[str, str]:
    """Extracts username -> password dictionary from forward_proxy basic_auth blocks."""
    users = {}
    matches = re.findall(r"^\s*basic_auth\s+([^\s]+)\s+([^\s]+)", caddyfile_content, re.MULTILINE)
    for u, p in matches:
        users[u] = p
    return users


def list_users(caddyfile_path: str = DEFAULT_CADDYFILE, lock_path: str = DEFAULT_LOCK_FILE) -> Dict[str, str]:
    """Reads existing NaiveProxy users safely."""
    if not os.path.exists(caddyfile_path):
        return {}
    with caddy_lock(lock_path):
        try:
            with open(caddyfile_path, "r", encoding="utf-8") as f:
                content = f.read()
            return parse_naive_users(content)
        except Exception as e:
            logger.error("Failed to read Caddyfile: %s", e)
            return {}


def atomic_modify_caddyfile(
    mutator_fn: Callable[[str], Tuple[str, any]],
    caddyfile_path: str = DEFAULT_CADDYFILE,
    backup_path: Optional[str] = None,
    control_runner: Callable[..., any] = default_control_runner,
    lock_path: str = DEFAULT_LOCK_FILE
) -> any:
    """
    Performs safe transactional mutation of Caddyfile:
    1. Lock acquisition.
    2. Read current configuration.
    3. Run in-memory mutator.
    4. Write temporary candidate file.
    5. Validate candidate via caddy validate.
    6. Backup current configuration.
    7. Atomically replace Caddyfile.
    8. Reload Caddy via control_runner.
    9. On failure: automated rollback.
    """
    if backup_path is None:
        backup_path = f"{caddyfile_path}.bak"

    with caddy_lock(lock_path):
        if not os.path.exists(caddyfile_path):
            raise CaddyManagerError(f"Caddyfile '{caddyfile_path}' not found.")

        with open(caddyfile_path, "r", encoding="utf-8") as f:
            original_content = f.read()

        modified_content, result = mutator_fn(original_content)

        # Temp candidate in same directory if writable, else /tmp
        cfg_dir = os.path.dirname(os.path.abspath(caddyfile_path))
        temp_dir = cfg_dir if os.access(cfg_dir, os.W_OK) else "/tmp"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=temp_dir,
                prefix="caddy_cand_",
                delete=False
            ) as tmp:
                tmp.write(modified_content)
                tmp_path = tmp.name

            # Pre-flight syntax validation
            try:
                control_runner("caddy-validate", tmp_path)
            except Exception as val_err:
                stderr_msg = getattr(val_err, "stderr", str(val_err))
                raise CaddyValidationError(f"Caddyfile syntax validation failed: {stderr_msg}") from val_err

            # Backup
            try:
                shutil.copy2(caddyfile_path, backup_path)
            except Exception as e:
                logger.warning("Could not create Caddyfile backup: %s", e)

            # Atomic replace
            try:
                os.replace(tmp_path, caddyfile_path)
            except OSError:
                atomic_tmp = f"{caddyfile_path}.atomic_{os.getpid()}"
                shutil.copy2(tmp_path, atomic_tmp)
                os.replace(atomic_tmp, caddyfile_path)

            # Reload and verify
            try:
                control_runner("caddy-reload")
                control_runner("caddy-status")
            except Exception as reload_err:
                logger.critical("FATAL: Caddy reload failed, initiating rollback: %s", reload_err)
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, caddyfile_path)
                    try:
                        control_runner("caddy-reload")
                    except Exception:
                        pass
                raise CaddyReloadError(f"Caddy reload failed: {reload_err}") from reload_err

            return result
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


def add_naive_user(
    username: str,
    password: Optional[str] = None,
    caddyfile_path: str = DEFAULT_CADDYFILE,
    control_runner: Callable[..., any] = default_control_runner,
    lock_path: str = DEFAULT_LOCK_FILE
) -> str:
    """Adds a new user to forward_proxy basic_auth block."""
    valid_user = validate_username(username)
    pwd = password or generate_secure_password(16)

    def _mutate(content: str) -> Tuple[str, str]:
        users = parse_naive_users(content)
        if valid_user in users:
            raise UserAlreadyExistsError(f"Пользователь '{valid_user}' уже существует.")

        # Find forward_proxy { block and insert basic_auth
        fp_pattern = re.compile(r"(\bforward_proxy\s*\{)(.*?)(\})", re.DOTALL)
        match = fp_pattern.search(content)
        if not match:
            raise CaddyManagerError("Блок 'forward_proxy {' не найден в Caddyfile.")

        prefix = match.group(1)
        body = match.group(2)
        suffix = match.group(3)

        new_auth_line = f"\n            basic_auth {valid_user} {pwd}"
        new_body = new_auth_line + body
        new_content = content[:match.start()] + prefix + new_body + suffix + content[match.end():]
        return new_content, pwd

    return atomic_modify_caddyfile(
        mutator_fn=_mutate,
        caddyfile_path=caddyfile_path,
        control_runner=control_runner,
        lock_path=lock_path
    )


def delete_naive_user(
    username: str,
    caddyfile_path: str = DEFAULT_CADDYFILE,
    control_runner: Callable[..., any] = default_control_runner,
    lock_path: str = DEFAULT_LOCK_FILE
) -> bool:
    """Deletes user from forward_proxy basic_auth block."""
    valid_user = validate_username(username)

    def _mutate(content: str) -> Tuple[str, bool]:
        users = parse_naive_users(content)
        if valid_user not in users:
            raise UserNotFoundError(f"Пользователь '{valid_user}' не найден.")

        # Remove the specific basic_auth line
        pattern = re.compile(rf"^\s*basic_auth\s+{re.escape(valid_user)}\s+[^\s]+\n?", re.MULTILINE)
        new_content = pattern.sub("", content)
        return new_content, True

    return atomic_modify_caddyfile(
        mutator_fn=_mutate,
        caddyfile_path=caddyfile_path,
        control_runner=control_runner,
        lock_path=lock_path
    )
