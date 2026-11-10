# ==============================================================================
# tests/test_caddy_manager.py — Unit Tests for Caddyfile Management & Bot Auth
# ==============================================================================
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure bot directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bot")))

from caddy_manager import (
    validate_username,
    generate_secure_password,
    parse_naive_users,
    list_users,
    add_naive_user,
    delete_naive_user,
    atomic_modify_caddyfile,
    load_users_meta,
    save_users_meta,
    get_user_by_tg_id,
    get_all_users_meta,
    InvalidUsernameError,
    UserAlreadyExistsError,
    UserNotFoundError,
    CaddyValidationError,
    CaddyReloadError,
    CaddyManagerError,
)
from auth import (
    parse_admin_ids,
    is_admin,
    admin_required,
    AuthorizationConfigurationError
)

SAMPLE_CADDYFILE = """
node.example.com {
    tls internal

    route {
        forward_proxy {
            basic_auth alice SecretPassword123
            basic_auth bob AnotherSecret456
            hide_ip
            hide_via
            probe_resistance secret.localhost
        }
        file_server {
            root /var/www/stealth
        }
    }
}
"""


class TestCaddyValidation(unittest.TestCase):
    def test_validate_username_valid(self):
        valid_cases = ["alice", "bob_123", "charlie-dev", "User99", "admin_ops-1"]
        for username in valid_cases:
            self.assertEqual(validate_username(username), username)

    def test_validate_username_invalid(self):
        invalid_cases = [
            "ab",                      # Too short (<3)
            "a" * 33,                  # Too long (>32)
            "user name",               # Contains whitespace
            "user@domain",             # Invalid symbol @
            "user$bad",                # Invalid symbol $
            "../traversal",            # Path traversal symbols
            "",                        # Empty
            "   ",                     # Only spaces
            "user;rm -rf /",           # Command injection attempt
        ]
        for username in invalid_cases:
            with self.assertRaises(InvalidUsernameError):
                validate_username(username)

    def test_generate_secure_password(self):
        pwd1 = generate_secure_password(16)
        pwd2 = generate_secure_password(16)
        self.assertEqual(len(pwd1), 16)
        self.assertEqual(len(pwd2), 16)
        self.assertNotEqual(pwd1, pwd2)


class TestCaddyParsing(unittest.TestCase):
    def test_parse_naive_users(self):
        users = parse_naive_users(SAMPLE_CADDYFILE)
        self.assertEqual(len(users), 2)
        self.assertEqual(users["alice"], "SecretPassword123")
        self.assertEqual(users["bob"], "AnotherSecret456")

    def test_parse_empty(self):
        users = parse_naive_users("empty content without users")
        self.assertEqual(users, {})


class TestCaddyMutation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.caddyfile_path = os.path.join(self.temp_dir.name, "Caddyfile")
        self.lock_path = os.path.join(self.temp_dir.name, "caddy.lock")
        self.meta_path = os.path.join(self.temp_dir.name, "users_meta.json")
        with open(self.caddyfile_path, "w", encoding="utf-8") as f:
            f.write(SAMPLE_CADDYFILE)
        self.mock_runner = MagicMock()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_users(self):
        users = list_users(self.caddyfile_path, lock_path=self.lock_path)
        self.assertIn("alice", users)
        self.assertIn("bob", users)
        self.assertEqual(len(users), 2)

    def test_add_naive_user_success(self):
        pwd = add_naive_user(
            "charlie",
            password="CustomPass123",
            caddyfile_path=self.caddyfile_path,
            control_runner=self.mock_runner,
            lock_path=self.lock_path
        )
        self.assertEqual(pwd, "CustomPass123")

        # Verify calls to control runner
        self.mock_runner.assert_any_call("caddy-validate", unittest.mock.ANY)
        self.mock_runner.assert_any_call("caddy-reload")
        self.mock_runner.assert_any_call("caddy-status")

        # Verify user is now present in file
        users = list_users(self.caddyfile_path, lock_path=self.lock_path)
        self.assertIn("charlie", users)
        self.assertEqual(users["charlie"], "CustomPass123")

    def test_add_user_with_metadata_and_lookup(self):
        meta = {
            "tg_id": 987654321,
            "tg_username": "proxy_user",
            "tg_first_name": "Proxy",
            "tg_last_name": "Member"
        }
        pwd = add_naive_user(
            "charlie_meta",
            password="CustomPass123",
            caddyfile_path=self.caddyfile_path,
            control_runner=self.mock_runner,
            lock_path=self.lock_path,
            extra_meta=meta,
            meta_path=self.meta_path
        )
        self.assertEqual(pwd, "CustomPass123")

        # Lookup by Telegram ID
        found = get_user_by_tg_id(987654321, meta_path=self.meta_path)
        self.assertIsNotNone(found)
        uname, udata = found
        self.assertEqual(uname, "charlie_meta")
        self.assertEqual(udata["tg_id"], 987654321)
        self.assertEqual(udata["tg_username"], "proxy_user")

        # Get all users meta
        all_meta = get_all_users_meta(meta_path=self.meta_path)
        self.assertIn("charlie_meta", all_meta)

        # Deleting user also cleans up metadata
        delete_naive_user(
            "charlie_meta",
            caddyfile_path=self.caddyfile_path,
            control_runner=self.mock_runner,
            lock_path=self.lock_path,
            meta_path=self.meta_path
        )
        self.assertIsNone(get_user_by_tg_id(987654321, meta_path=self.meta_path))

    def test_add_duplicate_user_raises(self):
        with self.assertRaises(UserAlreadyExistsError):
            add_naive_user(
                "alice",
                caddyfile_path=self.caddyfile_path,
                control_runner=self.mock_runner,
                lock_path=self.lock_path
            )

    def test_add_user_no_forward_proxy_block(self):
        bad_caddyfile = os.path.join(self.temp_dir.name, "Caddyfile.bad")
        with open(bad_caddyfile, "w", encoding="utf-8") as f:
            f.write("example.com { respond \"Hello\" }")

        with self.assertRaises(CaddyManagerError):
            add_naive_user(
                "dave",
                caddyfile_path=bad_caddyfile,
                control_runner=self.mock_runner,
                lock_path=self.lock_path
            )

    def test_delete_naive_user_success(self):
        result = delete_naive_user(
            "alice",
            caddyfile_path=self.caddyfile_path,
            control_runner=self.mock_runner,
            lock_path=self.lock_path
        )
        self.assertTrue(result)
        users = list_users(self.caddyfile_path, lock_path=self.lock_path)
        self.assertNotIn("alice", users)
        self.assertIn("bob", users)

    def test_delete_nonexistent_user_raises(self):
        with self.assertRaises(UserNotFoundError):
            delete_naive_user(
                "unknown_user",
                caddyfile_path=self.caddyfile_path,
                control_runner=self.mock_runner,
                lock_path=self.lock_path
            )

    def test_validation_failure_preserves_original(self):
        # Simulate caddy validate failure
        def failing_runner(action, *args):
            if action == "caddy-validate":
                raise Exception("Syntax error on line 42")
            return MagicMock()

        with self.assertRaises(CaddyValidationError):
            add_naive_user(
                "newguy",
                caddyfile_path=self.caddyfile_path,
                control_runner=failing_runner,
                lock_path=self.lock_path
            )

        # File should remain unchanged
        with open(self.caddyfile_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, SAMPLE_CADDYFILE)

    def test_reload_failure_triggers_rollback(self):
        # Simulate reload failure
        def failing_reload_runner(action, *args):
            if action == "caddy-reload":
                raise Exception("Failed to bind port")
            return MagicMock()

        with self.assertRaises(CaddyReloadError):
            add_naive_user(
                "newguy",
                caddyfile_path=self.caddyfile_path,
                control_runner=failing_reload_runner,
                lock_path=self.lock_path
            )

        # File should have rolled back to original content
        with open(self.caddyfile_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, SAMPLE_CADDYFILE)


class TestAuth(unittest.IsolatedAsyncioTestCase):
    def test_parse_admin_ids_valid(self):
        admins = parse_admin_ids("123456, 789012, 999")
        self.assertEqual(admins, {123456, 789012, 999})

    def test_parse_admin_ids_invalid(self):
        with self.assertRaises(AuthorizationConfigurationError):
            parse_admin_ids("")
        with self.assertRaises(AuthorizationConfigurationError):
            parse_admin_ids(None)
        with self.assertRaises(AuthorizationConfigurationError):
            parse_admin_ids("123, invalid_id, 456")
        with self.assertRaises(AuthorizationConfigurationError):
            parse_admin_ids("-5, 123")

    def test_is_admin(self):
        admins = {111, 222}
        self.assertTrue(is_admin(111, admins))
        self.assertFalse(is_admin(999, admins))
        self.assertFalse(is_admin(None, admins))
        self.assertFalse(is_admin(111, set()))

    async def test_admin_required_decorator_authorized(self):
        mock_handler = AsyncMock(return_value="success")
        decorated = admin_required({111})(mock_handler)

        mock_update = MagicMock()
        mock_update.effective_user.id = 111
        mock_update.callback_query = None
        mock_context = MagicMock()

        res = await decorated(mock_update, mock_context)
        self.assertEqual(res, "success")
        mock_handler.assert_awaited_once_with(mock_update, mock_context)

    async def test_admin_required_decorator_unauthorized(self):
        mock_handler = AsyncMock(return_value="should_not_run")
        decorated = admin_required({111})(mock_handler)

        mock_update = MagicMock()
        mock_update.effective_user.id = 999
        mock_update.callback_query = None
        mock_update.effective_message.reply_text = AsyncMock()
        mock_context = MagicMock()

        res = await decorated(mock_update, mock_context)
        self.assertIsNone(res)
        mock_handler.assert_not_awaited()
        mock_update.effective_message.reply_text.assert_awaited_once()


class TestCaddyPermissionsAndBackup(unittest.TestCase):
    def test_chmod_0644_before_validate_and_atomic_backup_overwrites_readonly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            caddyfile = os.path.join(tmpdir, "Caddyfile")
            backup = f"{caddyfile}.bak"
            lock_path = os.path.join(tmpdir, "lock")
            with open(caddyfile, "w") as f:
                f.write(SAMPLE_CADDYFILE)
            # Pre-existing read-only backup (simulates stale root-owned .bak in group-writable dir)
            with open(backup, "w") as f:
                f.write("stale backup")
            os.chmod(backup, 0o444)

            observed_modes = []
            def runner(action, target=None):
                if action == "caddy-validate" and target:
                    observed_modes.append(os.stat(target).st_mode & 0o777)
                return MagicMock(returncode=0, stdout="active", stderr="")

            def mutator(content):
                return content + "\n# mutated\n", True

            atomic_modify_caddyfile(mutator, caddyfile_path=caddyfile, control_runner=runner, lock_path=lock_path)
            self.assertEqual(observed_modes, [0o644])
            with open(backup, "r") as f:
                self.assertIn("alice", f.read())


class TestBotFormatting(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if "qrcode" not in sys.modules:
            sys.modules["qrcode"] = MagicMock()
        if "telegram" not in sys.modules:
            tg_mock = MagicMock()
            class MockBtn:
                def __init__(self, text, url=None, callback_data=None):
                    self.text = text
                    self.url = url
                    self.callback_data = callback_data
            class MockMarkup:
                def __init__(self, inline_keyboard):
                    self.inline_keyboard = inline_keyboard
            tg_mock.InlineKeyboardButton = MockBtn
            tg_mock.InlineKeyboardMarkup = MockMarkup
            tg_mock.ReplyKeyboardMarkup = MagicMock()
            tg_mock.InputFile = MagicMock()
            sys.modules["telegram"] = tg_mock
        if "telegram.ext" not in sys.modules:
            sys.modules["telegram.ext"] = MagicMock()

    def test_escape_md(self):
        from bot import escape_md
        self.assertEqual(escape_md("Olesya_Media"), r"Olesya\_Media")
        self.assertEqual(escape_md("user_test_name"), r"user\_test\_name")
        self.assertEqual(escape_md("*star*"), r"\*star\*")
        self.assertEqual(escape_md("`code`"), r"\`code\`")
        self.assertEqual(escape_md("[test]"), r"\[test]")
        self.assertEqual(escape_md(""), "")
        self.assertEqual(escape_md(None), "")

    async def test_list_users_and_info_card(self):
        import bot
        update = MagicMock()
        update.message = AsyncMock()
        update.effective_user.id = 12345
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": "12345"}), \
             patch.object(bot, "list_users", return_value={"tg_olesya_media": "secret123"}), \
             patch.object(bot, "get_all_users_meta", return_value={
                 "tg_olesya_media": {"tg_username": "Olesya_Media", "tg_first_name": "Olesya", "tg_id": 2103}
             }):
            await bot.list_users_command(update, context)

            kwargs = update.message.reply_text.call_args.kwargs
            args = update.message.reply_text.call_args.args
            msg = args[0] if args else kwargs.get("text", "")
            markup = kwargs["reply_markup"]
            self.assertIn(r"@Olesya\_Media", msg)
            callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertEqual(callbacks, ["info_tg_olesya_media", "del_tg_olesya_media"])

            # Test info_ callback
            cb_update = MagicMock()
            cb_update.callback_query = AsyncMock()
            cb_update.callback_query.data = "info_tg_olesya_media"
            cb_update.callback_query.message = AsyncMock()
            cb_update.effective_user.id = 12345
            await bot.callback_handler(cb_update, context)

            info_args = cb_update.callback_query.message.reply_text.call_args.args
            info_kwargs = cb_update.callback_query.message.reply_text.call_args.kwargs
            info_msg = info_args[0] if info_args else info_kwargs.get("text", "")
            self.assertIn("secret123", info_msg)
            self.assertIn("config.json", info_msg)
            self.assertIn(r"@Olesya\_Media", info_msg)

    async def test_long_caption_sends_photo_and_full_text_without_truncation(self):
        import io
        import bot
        target = AsyncMock()
        long_caption = "Header\n" + ("a" * 1100)
        await bot.send_safe_photo_or_text(target, io.BytesIO(b"png"), "qr.png", long_caption)
        target.reply_photo.assert_awaited_once()
        target.reply_text.assert_awaited_once()
        sent_text = target.reply_text.call_args.args[0]
        self.assertEqual(sent_text, long_caption)


if __name__ == "__main__":
    unittest.main()
