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
vpn.example.com {
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
            "tg_username": "cascade_user",
            "tg_first_name": "Cascade",
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
        self.assertEqual(udata["tg_username"], "cascade_user")

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


if __name__ == "__main__":
    unittest.main()
