import unittest
from cleaner import transform_text, should_process_message

class TestCleanerUpgrades(unittest.TestCase):
    def test_global_url_replacement(self):
        custom = {
            "replace_all_urls": True,
            "global_replacement_url": "https://t.me/my_promo_admin",
            "url_rules": []
        }
        text = "Visit https://old-casino.com/promo or https://t.me/old_channel for bonus!"
        transformed = transform_text(text, custom)
        self.assertNotIn("https://old-casino.com/promo", transformed)
        self.assertNotIn("https://t.me/old_channel", transformed)
        self.assertIn("https://t.me/my_promo_admin", transformed)

    def test_specific_url_mapping(self):
        custom = {
            "replace_all_urls": False,
            "url_rules": [
                {"match": "https://old-link.com", "replace": "https://my-affiliate.com"}
            ]
        }
        text = "Check out https://old-link.com and https://neutral-link.com"
        transformed = transform_text(text, custom)
        self.assertIn("https://my-affiliate.com", transformed)
        self.assertIn("https://neutral-link.com", transformed)
        self.assertNotIn("https://old-link.com", transformed)

    def test_global_username_replacement(self):
        custom = {
            "replace_all_usernames": True,
            "global_replacement_username": "@my_admin_support"
        }
        text = "Contact @old_owner or @original_bot for VIP access."
        transformed = transform_text(text, custom)
        self.assertNotIn("@old_owner", transformed)
        self.assertNotIn("@original_bot", transformed)
        self.assertIn("@my_admin_support", transformed)

    def test_specific_username_mapping(self):
        custom = {
            "replace_all_usernames": False,
            "username_rules": [
                {"match": "@old_support", "replace": "@new_support"}
            ]
        }
        text = "For help message @old_support or @general_chat"
        transformed = transform_text(text, custom)
        self.assertIn("@new_support", transformed)
        self.assertIn("@general_chat", transformed)
        self.assertNotIn("@old_support", transformed)

    def test_header_footer_injection(self):
        custom = {
            "header": "🔥 BREAKING SIGNAL 🔥",
            "footer": "👉 Contact Admin: @my_admin"
        }
        text = "BTC crossed 70,000 USD."
        transformed = transform_text(text, custom)
        self.assertTrue(transformed.startswith("🔥 BREAKING SIGNAL 🔥"))
        self.assertTrue(transformed.endswith("👉 Contact Admin: @my_admin"))

    def test_keyword_filters(self):
        custom = {
            "blacklist_keywords": ["scam", "phishing"],
            "whitelist_keywords": ["vip", "signal"]
        }
        # Blocked by blacklist
        allowed, reason = should_process_message("This is a scam offer", custom)
        self.assertFalse(allowed)

        # Blocked because doesn't match whitelist
        allowed, reason = should_process_message("Hello regular world", custom)
        self.assertFalse(allowed)

        # Allowed because matches whitelist and no blacklist
        allowed, reason = should_process_message("New VIP crypto alert", custom)
        self.assertTrue(allowed)

class TestCloudDeployment(unittest.TestCase):
    def test_healthz_endpoint(self):
        from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
        from web_server import create_app
        import asyncio

        async def _test():
            app = create_app()
            from aiohttp.test_utils import TestClient, TestServer
            client = TestClient(TestServer(app))
            await client.start_server()
            resp = await client.get("/healthz")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data.get("status"), "healthy")
            await client.close()

        asyncio.run(_test())

    def test_settings_session_string(self):
        from settings_manager import settings_manager
        tg = settings_manager.get("telegram", {})
        self.assertIn("session_string", tg)

class TestImageHandling(unittest.TestCase):
    def test_is_image_message_photo(self):
        from cleaner import is_image_message
        class DummyPhotoMessage:
            photo = object()
            document = None
            media = None
        self.assertTrue(is_image_message(DummyPhotoMessage()))

    def test_is_image_message_doc_image(self):
        from cleaner import is_image_message
        class DummyDoc:
            mime_type = "image/png"
            attributes = []
        class DummyDocMessage:
            photo = None
            document = DummyDoc()
            media = None
        self.assertTrue(is_image_message(DummyDocMessage()))

    def test_is_image_message_non_image(self):
        from cleaner import is_image_message
        class DummyPdfDoc:
            mime_type = "application/pdf"
            attributes = []
        class DummyPdfMessage:
            photo = None
            document = DummyPdfDoc()
            video = None
            media = None
        self.assertFalse(is_image_message(DummyPdfMessage()))

        class DummyTextOnly:
            photo = None
            document = None
            media = None
        self.assertFalse(is_image_message(DummyTextOnly()))

    def test_settings_manager_remove_images(self):
        from settings_manager import SettingsManager
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            json_file = os.path.join(tmpdir, "test_settings.json")
            sm = SettingsManager(filepath=json_file)

            # Test toggling to False
            sm.update_section("bot", {"remove_images": False})
            self.assertFalse(sm.get("bot", {}).get("remove_images"))
            render_dict = sm.generate_render_env_dict()
            self.assertEqual(render_dict["REMOVE_IMAGES"], "false")

            # Test toggling to True
            sm.update_section("bot", {"remove_images": True})
            self.assertTrue(sm.get("bot", {}).get("remove_images"))
            render_dict = sm.generate_render_env_dict()
            self.assertEqual(render_dict["REMOVE_IMAGES"], "true")

class TestChannelMatching(unittest.TestCase):
    def test_clean_channel_identifier(self):
        from bot_engine import _clean_channel_identifier
        self.assertEqual(_clean_channel_identifier("-1002320352126"), -1002320352126)
        self.assertEqual(_clean_channel_identifier("2320352126"), 2320352126)
        self.assertEqual(_clean_channel_identifier("@ballerrz"), "@ballerrz")
        self.assertEqual(_clean_channel_identifier("ballerrz"), "@ballerrz")
        self.assertEqual(_clean_channel_identifier("https://t.me/ballerrz"), "@ballerrz")
        self.assertEqual(_clean_channel_identifier("t.me/scotttrad"), "@scotttrad")
        self.assertEqual(_clean_channel_identifier("+abcdefg123"), "+abcdefg123")

    def test_split_and_clean_channels(self):
        from bot_engine import _split_and_clean_channels
        raw = "@ballerrz, -1002497672223; https://t.me/scotttrad\n@twssignals"
        cleaned = _split_and_clean_channels(raw)
        self.assertEqual(cleaned, ["@ballerrz", -1002497672223, "@scotttrad", "@twssignals"])

        # Also test list of mixed items
        mixed = ["@chan1, @chan2", -10012345]
        self.assertEqual(_split_and_clean_channels(mixed), ["@chan1", "@chan2", -10012345])

    def test_is_chat_match(self):
        from bot_engine import _is_chat_match

        # Direct int match
        self.assertTrue(_is_chat_match(-1002320352126, -1002320352126))
        # Raw int vs -100 int
        self.assertTrue(_is_chat_match("2320352126", -1002320352126))
        self.assertTrue(_is_chat_match("-1002320352126", -1002320352126))

        # Mock entity
        class MockEntity:
            id = 2320352126
            username = "ballerrz"
            title = "Ballerrz Signals"

        mock_ent = MockEntity()
        # Should match by username
        self.assertTrue(_is_chat_match("@ballerrz", -1002320352126, mock_ent))
        self.assertTrue(_is_chat_match("ballerrz", -1002320352126, mock_ent))
        self.assertTrue(_is_chat_match("https://t.me/ballerrz", -1002320352126, mock_ent))

        # Different channel should NOT match
        self.assertFalse(_is_chat_match("@otherchan", -1002320352126, mock_ent))
        self.assertFalse(_is_chat_match(-1009999999, -1002320352126, mock_ent))

    def test_get_destinations_for_chat_multiple(self):
        import asyncio
        from bot_engine import BotEngine
        from unittest.mock import MagicMock

        bot = BotEngine()
        bot.client = MagicMock()
        bot.client.is_connected.return_value = True

        class MockChannel:
            def __init__(self, cid, title, username=None):
                self.id = cid
                self.title = title
                self.username = username

        src_ent = MockChannel(2320352126, "Source Signals", "ballerrz")
        dst1_ent = MockChannel(2497672223, "Dest Channel 1")
        dst2_ent = MockChannel(2999888777, "Dest Channel 2", "second_dest")

        bot.resolved_sources = {"@ballerrz": src_ent}
        bot.resolved_destinations = {
            "-1002497672223": dst1_ent,
            "@second_dest": dst2_ent
        }

        async def _test():
            from settings_manager import settings_manager
            orig_channels = settings_manager.get("channels", {})
            try:
                settings_manager.update_section("channels", {
                    "sources": ["@ballerrz"],
                    "destinations": ["-1002497672223", "@second_dest"],
                    "mappings": {}
                })
                # Incoming message from ballerrz (chat_id = -1002320352126)
                dests = await bot._get_destinations_for_chat(-1002320352126)
                self.assertEqual(len(dests), 2)
                self.assertIn(dst1_ent, dests)
                self.assertIn(dst2_ent, dests)

                # Incoming message from unmonitored channel
                dests_unmonitored = await bot._get_destinations_for_chat(-1009999999999)
                self.assertEqual(len(dests_unmonitored), 0)
            finally:
                settings_manager.update_section("channels", orig_channels)

        asyncio.run(_test())

class TestDashboardAuthAndSecurity(unittest.TestCase):
    def test_password_hashing_and_verification(self):
        from auth import hash_password, _verify_hash
        pwd = "SecretPassword123!"
        hashed = hash_password(pwd)
        self.assertTrue(hashed.startswith("pbkdf2$"))
        self.assertTrue(_verify_hash(pwd, hashed))
        self.assertFalse(_verify_hash("WrongPassword", hashed))

    def test_auth_and_protected_endpoints(self):
        from aiohttp.test_utils import TestClient, TestServer
        from web_server import create_app
        from settings_manager import settings_manager
        import asyncio

        async def _test():
            orig_web = dict(settings_manager.get("web", {}))
            try:
                settings_manager.update_section("web", {"admin_password_hash": ""})
                app = create_app()
                client = TestClient(TestServer(app))
                await client.start_server()

                # 1. State endpoint should indicate setup_required
                resp = await client.get("/api/auth/state")
                self.assertEqual(resp.status, 200)
                data = await resp.json()
                self.assertTrue(data.get("setup_required"))
                self.assertFalse(data.get("authenticated"))

                # 2. Protected endpoints should be blocked with 401
                resp = await client.get("/api/settings")
                self.assertEqual(resp.status, 401)
                data = await resp.json()
                self.assertEqual(data.get("error"), "auth_required")

                # 3. Setup rejects short passwords (<8 chars)
                resp = await client.post("/api/auth/setup", json={"password": "short"})
                self.assertEqual(resp.status, 400)

                # 4. Setup succeeds with >=8 chars password
                resp = await client.post("/api/auth/setup", json={"password": "DashboardPassword123"})
                self.assertEqual(resp.status, 200)
                setup_data = await resp.json()
                self.assertTrue(setup_data.get("success"))

                # 5. Accessing protected endpoint with authenticated session cookie works
                resp = await client.get("/api/settings")
                self.assertEqual(resp.status, 200)
                settings_data = await resp.json()
                self.assertIn("telegram", settings_data)
                # Verify secrets are scrubbed
                self.assertNotIn("session_string", settings_data.get("telegram", {}))
                self.assertNotIn("api_hash", settings_data.get("telegram", {}))
                self.assertNotIn("bot_token", settings_data.get("telegram", {}))
                self.assertNotIn("web", settings_data)

                # 6. Logout terminates session
                resp = await client.post("/api/auth/logout")
                self.assertEqual(resp.status, 200)

                # 7. Protected endpoint is blocked again
                resp = await client.get("/api/settings")
                self.assertEqual(resp.status, 401)

                # 8. Login with wrong password fails
                resp = await client.post("/api/auth/login", json={"password": "WrongPassword"})
                self.assertEqual(resp.status, 401)

                # 9. Login with correct password succeeds
                resp = await client.post("/api/auth/login", json={"password": "DashboardPassword123"})
                self.assertEqual(resp.status, 200)

                await client.close()
            finally:
                settings_manager.update_section("web", orig_web)

        asyncio.run(_test())

class TestResetAndEnvironment(unittest.TestCase):
    def test_environment_validation_and_reset(self):
        from aiohttp.test_utils import TestClient, TestServer
        from web_server import create_app
        from settings_manager import settings_manager
        from auth import hash_password
        import asyncio

        async def _test():
            orig_web = dict(settings_manager.get("web", {}))
            orig_app = dict(settings_manager.get("app", {}))
            try:
                pwd_hash = hash_password("ValidPassword123")
                settings_manager.update_section("web", {"admin_password_hash": pwd_hash})
                app = create_app()
                client = TestClient(TestServer(app))
                await client.start_server()

                # Authenticate
                login_resp = await client.post("/api/auth/login", json={"password": "ValidPassword123"})
                self.assertEqual(login_resp.status, 200)

                # Change environment to development
                resp = await client.post("/api/settings", json={"app": {"environment": "development"}})
                self.assertEqual(resp.status, 200)
                data = await resp.json()
                self.assertEqual(data.get("environment"), "development")
                self.assertEqual(settings_manager.get_environment(), "development")

                # Invalid environment rejected
                resp = await client.post("/api/settings", json={"app": {"environment": "invalid_mode"}})
                self.assertEqual(resp.status, 400)

                # Switch back to production
                resp = await client.post("/api/settings", json={"app": {"environment": "production"}})
                self.assertEqual(resp.status, 200)
                self.assertEqual(settings_manager.get_environment(), "production")

                # Test Reset confirmation validation
                resp = await client.post("/api/reset", json={"confirm": "WRONG"})
                self.assertEqual(resp.status, 400)
                err_data = await resp.json()
                self.assertIn("RESET", err_data.get("error", ""))

                # Successful Reset with "RESET"
                resp = await client.post("/api/reset", json={"confirm": "RESET", "terminate_session": False})
                self.assertEqual(resp.status, 200)
                reset_data = await resp.json()
                self.assertTrue(reset_data.get("success"))

                # Settings should be fresh, but admin password preserved
                self.assertFalse(settings_manager.is_telegram_configured())
                self.assertTrue(bool(settings_manager.get("web", {}).get("admin_password_hash")))

                await client.close()
            finally:
                settings_manager.update_section("web", orig_web)
                settings_manager.update_section("app", orig_app)

        asyncio.run(_test())

class TestTelegramLoginFlowValidation(unittest.TestCase):
    def test_telegram_send_code_validation(self):
        from aiohttp.test_utils import TestClient, TestServer
        from web_server import create_app
        from settings_manager import settings_manager
        from auth import hash_password
        import asyncio

        async def _test():
            orig_web = dict(settings_manager.get("web", {}))
            try:
                pwd_hash = hash_password("ValidPassword123")
                settings_manager.update_section("web", {"admin_password_hash": pwd_hash})
                app = create_app()
                client = TestClient(TestServer(app))
                await client.start_server()

                await client.post("/api/auth/login", json={"password": "ValidPassword123"})

                # Missing credentials
                resp = await client.post("/api/telegram/send-code", json={
                    "api_id": "",
                    "api_hash": "",
                    "phone": ""
                })
                self.assertEqual(resp.status, 400)
                data = await resp.json()
                self.assertIn("API ID", data.get("error", ""))

                # Invalid API ID
                resp = await client.post("/api/telegram/send-code", json={
                    "api_id": "not-a-number",
                    "api_hash": "somehash",
                    "phone": "+1234567890"
                })
                self.assertEqual(resp.status, 400)
                data = await resp.json()
                self.assertIn("number", data.get("error", ""))

                # Missing phone
                resp = await client.post("/api/telegram/send-code", json={
                    "api_id": "1234567",
                    "api_hash": "somehash",
                    "phone": ""
                })
                self.assertEqual(resp.status, 400)

                # Bot token validation
                resp = await client.post("/api/telegram/bot-token", json={
                    "api_id": "1234567",
                    "api_hash": "somehash",
                    "bot_token": ""
                })
                self.assertEqual(resp.status, 400)

                await client.close()
            finally:
                settings_manager.update_section("web", orig_web)

        asyncio.run(_test())

if __name__ == "__main__":
    unittest.main()
