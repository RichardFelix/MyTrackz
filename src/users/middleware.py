from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

# Reachable while a temporary password is still in place: the page that
# replaces it, and logging out.
ALLOWED_URL_NAMES = {"change_temporary_password", "account_logout"}


class ForcePasswordChangeMiddleware:
    """Hold accounts with a temporary password on the password change page."""

    def __init__(self, get_response):
        """Initialize the middleware with the get_response callable."""
        self.get_response = get_response

    def __call__(self, request):
        """Pass the request through; the check happens in process_view."""
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):  # noqa: ARG002
        """Redirect every other page until the temporary password is replaced."""
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.must_change_password:
            return None

        match = request.resolver_match
        if match is not None and match.url_name in ALLOWED_URL_NAMES:
            return None

        target = reverse("change_temporary_password")
        if request.headers.get("HX-Request") == "true":
            # a redirect inside an htmx swap would render the page in a fragment
            response = HttpResponse(status=200)
            response["HX-Redirect"] = target
            return response
        return redirect(target)
