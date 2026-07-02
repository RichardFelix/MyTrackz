from django.contrib.auth import get_user_model
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from app.middleware import AutoLoginMiddleware, ProviderAPIErrorMiddleware
from app.models import Sources
from app.providers.services import ProviderAPIError

UserModel = get_user_model()


class AutoLoginMiddlewareTest(TestCase):
    """Test cases for AutoLoginMiddleware."""

    def setUp(self):
        """Create test users."""
        self.factory = RequestFactory()
        self.existing_active_user = UserModel.objects.create_user(
            username="active_user",
            password="active_user_password",  # noqa: S106
            is_active=True,
        )
        self.existing_inactive_user = UserModel.objects.create_user(
            username="inactive_user",
            password="inactive_user_password",  # noqa: S106
            is_active=False,
        )

    def get_request(self):
        """Return a request with session and user middleware applied."""
        request = self.factory.get("/")
        SessionMiddleware(lambda _request: None).process_request(request)
        AuthenticationMiddleware(lambda _request: None).process_request(request)
        return request

    def run_middleware(self, request):
        """Run auto-login middleware against the request."""
        middleware = AutoLoginMiddleware(lambda _request: None)
        middleware(request)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME=None)
    def test_env_var_unset(self):
        """Test that no auto-login occurs when YAMTRACK_AUTO_LOGIN_USERNAME is unset."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="active_user")
    def test_existing_active_user(self):
        """Test that auto-login works with an existing active user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertTrue(request.user.is_authenticated)
        self.assertEqual(request.user, self.existing_active_user)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="missing_user")
    def test_missing_user(self):
        """Test that no auto-login occurs with a missing user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="inactive_user")
    def test_inactive_user(self):
        """Test that no auto-login occurs with an inactive user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)


class ProviderAPIErrorMiddlewareTest(TestCase):
    """Test cases for ProviderAPIErrorMiddleware."""

    def setUp(self):
        """Set up the request factory and middleware instance."""
        self.factory = RequestFactory()
        self.middleware = ProviderAPIErrorMiddleware(lambda _request: None)
        self.exception = ProviderAPIError(Sources.TMDB.value, Exception("boom"))

    def get_request(self, **extra):
        """Return a request with session, auth, and message middleware applied."""
        request = self.factory.get("/discover/recommendations", **extra)
        SessionMiddleware(lambda _request: None).process_request(request)
        AuthenticationMiddleware(lambda _request: None).process_request(request)
        MessageMiddleware(lambda _request: None).process_request(request)
        return request

    def test_non_htmx_request_renders_full_error_page(self):
        """A regular request should get the full styled 500 page."""
        request = self.get_request()

        response = self.middleware.process_exception(request, self.exception)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("HX-Redirect", response)

    def test_htmx_request_redirects_to_current_page(self):
        """An htmx request should trigger a full-page reload, not a fragment swap."""
        request = self.get_request(
            HTTP_HX_REQUEST="true",
            HTTP_HX_CURRENT_URL="http://testserver/discover",
        )

        response = self.middleware.process_exception(request, self.exception)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "http://testserver/discover")

    def test_htmx_request_without_current_url_falls_back_to_home(self):
        """Fall back to the home page if htmx didn't send the current URL."""
        request = self.get_request(HTTP_HX_REQUEST="true")

        response = self.middleware.process_exception(request, self.exception)

        self.assertEqual(response["HX-Redirect"], reverse("home"))
