import os

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import expect, sync_playwright


class IntegrationTest(StaticLiveServerTestCase):
    """Integration tests for the application."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # use headless=False, slow_mo=400 to see the browser
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        super().tearDownClass()
        cls.browser.close()
        cls.playwright.stop()

    def test_register_login(self):
        """Test the register and login pages."""
        page = self.browser.new_page()
        page.goto(f"{self.live_server_url}/")

        # Register
        page.get_by_role("link", name="Register now").click()
        page.get_by_placeholder("Choose a username").fill("test1234")
        page.get_by_placeholder("Create a password").fill("12341234")
        page.get_by_placeholder("Confirm your password").fill("12341234")
        page.get_by_role("button", name="Create account").click()
        expect(page.locator("body")).to_contain_text(
            "Your account has been created, you can now log in!",
        )

        # Login
        page.get_by_placeholder("Enter your username").click()
        page.get_by_placeholder("Enter your username").fill("test1234")
        page.get_by_placeholder("Enter your password").click()
        page.get_by_placeholder("Enter your password").fill("12341234")
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("main")).to_contain_text("No media in progress")

        # Logout
        page.get_by_role("button", name="Logout").click()
        expect(page.locator("body")).to_contain_text("Sign in to your account")
