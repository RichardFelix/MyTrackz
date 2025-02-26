from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from users import views

urlpatterns = [
    path("settings/account", views.account, name="account"),
    path("settings/sidebar", views.sidebar, name="sidebar"),
    path("login", views.CustomLoginView.as_view(), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "delete_import_schedule",
        views.delete_import_schedule,
        name="delete_import_schedule",
    ),
    path("regenerate_token", views.regenerate_token, name="regenerate_token"),
]

if settings.REGISTRATION:
    urlpatterns.append(path("register", views.register, name="register"))
