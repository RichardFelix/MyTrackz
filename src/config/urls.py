"""Yamtrack base URL Configuration.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/stable/topics/http/urls/

"""

from allauth.account import views as allauth_account_views
from allauth.mfa.base import views as allauth_mfa_views
from allauth.socialaccount import views as allauth_social_account_views
from allauth.urls import build_provider_urlpatterns
from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path("", include("app.urls")),
    path("", include("integrations.urls")),
    path("", include("users.urls")),
    path("", include("lists.urls")),
    path("", include("events.urls")),
    path("select2/", include("django_select2.urls")),
    path("health", include("health_check.urls")),
    path(
        "accounts/",
        include(
            [
                # see allauth/account/urls.py
                # login, logout, signup, account_inactive
                path("login/", allauth_account_views.login, name="account_login"),
                path("logout/", allauth_account_views.logout, name="account_logout"),
                path("signup/", allauth_account_views.signup, name="account_signup"),
                path(
                    "account_inactive/",
                    allauth_account_views.account_inactive,
                    name="account_inactive",
                ),
                # social account base urls, see allauth/socialaccount/urls.py
                path(
                    "3rdparty/",
                    include(
                        [
                            path(
                                "login/cancelled/",
                                allauth_social_account_views.login_cancelled,
                                name="socialaccount_login_cancelled",
                            ),
                            path(
                                "login/error/",
                                allauth_social_account_views.login_error,
                                name="socialaccount_login_error",
                            ),
                            path(
                                "signup/",
                                allauth_social_account_views.signup,
                                name="socialaccount_signup",
                            ),
                            path(
                                "",
                                allauth_social_account_views.connections,
                                name="socialaccount_connections",
                            ),
                        ],
                    ),
                ),
                *build_provider_urlpatterns(),
                path(
                    "2fa/authenticate/",
                    allauth_mfa_views.authenticate,
                    name="mfa_authenticate",
                ),
            ],
        ),
    ),
]


if settings.DEBUG:
    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
