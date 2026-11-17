import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from integrations.imports import helpers


class ImportViewTestCase(TestCase):
    """Shared setup for the import and export view tests."""

    def setUp(self):
        """Create and log in a user."""
        credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.client.login(**credentials)

    def assert_message(self, response, text):
        """Assert the response queued a message with the given text."""
        queued = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(text, queued)

    def set_oauth_state(self, token, frequency, **extra):
        """Store an OAuth state in the session, as the authorize views do."""
        session = self.client.session
        session[token] = {
            "mode": "new",
            "frequency": frequency,
            "time": "14:30",
            **extra,
        }
        session.save()


class UsernameImportViewTests(ImportViewTestCase):
    """Test the imports that are started from a username or account ID."""

    # url name, task, source label, message when the username is missing
    cases = [
        (
            "import_mal",
            "import_mal",
            "MyAnimeList",
            "MyAnimeList username is required.",
        ),
        ("import_kitsu", "import_kitsu", "Kitsu", "Kitsu user ID is required."),
        ("import_steam", "import_steam", "Steam", "Steam ID is required."),
        (
            "import_trakt_public",
            "import_trakt",
            "Trakt",
            "Trakt username is required.",
        ),
        (
            "import_anilist_public",
            "import_anilist",
            "AniList",
            "AniList username is required.",
        ),
    ]

    def test_missing_username_is_rejected(self):
        """Test no task is queued or scheduled without a username."""
        for url_name, task_name, _, error_message in self.cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.tasks.{task_name}.delay") as mock_delay,
            ):
                response = self.client.post(
                    reverse(url_name),
                    {"mode": "new", "frequency": "once", "time": "14:30"},
                )

                self.assertRedirects(response, reverse("import_data"))
                self.assert_message(response, error_message)
                mock_delay.assert_not_called()
        self.assertFalse(PeriodicTask.objects.exists())

    def test_one_time_import_queues_task(self):
        """Test a one-time import queues the task for the logged-in user."""
        for url_name, task_name, _, _ in self.cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.tasks.{task_name}.delay") as mock_delay,
            ):
                response = self.client.post(
                    reverse(url_name),
                    {
                        "user": "someone",
                        "mode": "overwrite",
                        "frequency": "once",
                        "time": "14:30",
                    },
                )

                self.assertRedirects(response, reverse("import_data"))
                mock_delay.assert_called_once_with(
                    username="someone",
                    user_id=self.user.id,
                    mode="overwrite",
                )
        self.assertFalse(PeriodicTask.objects.exists())

    def test_recurring_import_creates_schedule(self):
        """Test a recurring import creates a periodic task instead of queueing."""
        for url_name, task_name, source, _ in self.cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.tasks.{task_name}.delay") as mock_delay,
            ):
                response = self.client.post(
                    reverse(url_name),
                    {
                        "user": "someone",
                        "mode": "new",
                        "frequency": "daily",
                        "time": "14:30",
                    },
                )

                self.assertRedirects(response, reverse("import_data"))
                self.assert_message(response, f"{source} import task scheduled.")
                mock_delay.assert_not_called()

                periodic_task = PeriodicTask.objects.get(task=f"Import from {source}")
                self.assertEqual(
                    json.loads(periodic_task.kwargs),
                    {"username": "someone", "user_id": self.user.id, "mode": "new"},
                )
                self.assertEqual(periodic_task.crontab.day_of_week, "*")

    def test_every_other_day_schedule(self):
        """Test a non-daily frequency runs every second day."""
        self.client.post(
            reverse("import_mal"),
            {
                "user": "someone",
                "mode": "new",
                "frequency": "every_2_days",
                "time": "06:05",
            },
        )

        periodic_task = PeriodicTask.objects.get(task="Import from MyAnimeList")
        self.assertEqual(periodic_task.crontab.day_of_week, "*/2")
        self.assertEqual(periodic_task.crontab.minute, "5")

    def test_duplicate_schedule_is_rejected(self):
        """Test the same schedule cannot be created twice."""
        data = {"user": "someone", "mode": "new", "frequency": "daily", "time": "14:30"}
        self.client.post(reverse("import_kitsu"), data)
        response = self.client.post(reverse("import_kitsu"), data)

        self.assert_message(response, "The same import task is already scheduled.")
        self.assertEqual(PeriodicTask.objects.count(), 1)

    def test_invalid_time_is_rejected(self):
        """Test a malformed time does not create a schedule."""
        response = self.client.post(
            reverse("import_steam"),
            {"user": "someone", "mode": "new", "frequency": "daily", "time": "noon"},
        )

        self.assert_message(response, "Invalid import time.")
        self.assertFalse(PeriodicTask.objects.exists())

    def test_import_requires_post(self):
        """Test the import views reject GET requests."""
        for url_name, _, _, _ in self.cases:
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 405)


class FileImportViewTests(ImportViewTestCase):
    """Test the imports that are started from an uploaded CSV file."""

    # url name, task, form field, message when the file is missing
    cases = [
        (
            "import_yamtrack",
            "import_yamtrack",
            "yamtrack_csv",
            "MyTrackz CSV file is required.",
        ),
        (
            "import_hltb",
            "import_hltb",
            "hltb_csv",
            "HowLongToBeat CSV file is required.",
        ),
        ("import_imdb", "import_imdb", "imdb_csv", "IMDB CSV file is required."),
        (
            "import_goodreads",
            "import_goodreads",
            "goodreads_csv",
            "GoodReads CSV file is required.",
        ),
    ]

    def test_missing_file_is_rejected(self):
        """Test no task is queued without an uploaded file."""
        for url_name, task_name, _, error_message in self.cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.tasks.{task_name}.delay") as mock_delay,
            ):
                response = self.client.post(reverse(url_name), {"mode": "new"})

                self.assertRedirects(response, reverse("import_data"))
                self.assert_message(response, error_message)
                mock_delay.assert_not_called()

    def test_uploaded_file_queues_task(self):
        """Test the uploaded file is handed to the task for the logged-in user."""
        for url_name, task_name, field, _ in self.cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.tasks.{task_name}.delay") as mock_delay,
            ):
                upload = SimpleUploadedFile(
                    "export.csv",
                    b"title\nExample\n",
                    content_type="text/csv",
                )
                response = self.client.post(
                    reverse(url_name),
                    {"mode": "overwrite", field: upload},
                )

                self.assertRedirects(response, reverse("import_data"))
                mock_delay.assert_called_once()
                kwargs = mock_delay.call_args.kwargs
                self.assertEqual(kwargs["user_id"], self.user.id)
                self.assertEqual(kwargs["mode"], "overwrite")
                self.assertEqual(kwargs["file"].name, "export.csv")


class OAuthStartViewTests(ImportViewTestCase):
    """Test the views that send the user to a provider to authorize."""

    @override_settings(URLS=["https://mytrackz.example.com"], SIMKL_ID="simkl-client")
    def test_simkl_oauth_redirects_to_provider(self):
        """Test SIMKL authorization carries the client, callback and state."""
        response = self.client.post(
            reverse("simkl_oauth"),
            {"mode": "new", "frequency": "daily", "time": "14:30"},
        )

        self.assertEqual(response.status_code, 302)
        redirect = urlparse(response["Location"])
        query = parse_qs(redirect.query)
        self.assertEqual(redirect.netloc, "simkl.com")
        self.assertEqual(redirect.path, "/oauth/authorize")
        self.assertEqual(query["client_id"], ["simkl-client"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://mytrackz.example.com/import/simkl_private"],
        )
        self.assertEqual(
            self.client.session[query["state"][0]],
            {"mode": "new", "frequency": "daily", "time": "14:30"},
        )

    @override_settings(
        URLS=["https://mytrackz.example.com"],
        ANILIST_ID="anilist-client",
    )
    def test_anilist_oauth_redirects_to_provider(self):
        """Test AniList authorization carries the client, callback and state."""
        response = self.client.post(
            reverse("import_anilist_oauth"),
            {"mode": "overwrite", "frequency": "once", "time": "09:00"},
        )

        self.assertEqual(response.status_code, 302)
        redirect = urlparse(response["Location"])
        query = parse_qs(redirect.query)
        self.assertEqual(redirect.netloc, "anilist.co")
        self.assertEqual(redirect.path, "/api/v2/oauth/authorize")
        self.assertEqual(query["client_id"], ["anilist-client"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://mytrackz.example.com/import/anilist/private"],
        )
        self.assertEqual(
            self.client.session[query["state"][0]],
            {"mode": "overwrite", "frequency": "once", "time": "09:00"},
        )


class OAuthCallbackViewTests(ImportViewTestCase):
    """Test the views the providers redirect back to after authorization."""

    @patch("integrations.views.tasks.import_simkl.delay")
    @patch("integrations.views.simkl.get_token")
    def test_simkl_callback_queues_one_time_import(self, mock_get_token, mock_delay):
        """Test a one-time SIMKL import is queued with an encrypted token."""
        mock_get_token.return_value = {
            "access_token": "simkl-access",
            "username": "simkl-user",
        }
        self.set_oauth_state("state-token", "once")

        response = self.client.get(
            reverse("import_simkl_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["mode"], "new")
        self.assertNotEqual(kwargs["token"], "simkl-access")
        self.assertEqual(helpers.decrypt(kwargs["token"]), "simkl-access")

    @patch("integrations.views.tasks.import_simkl.delay")
    @patch("integrations.views.simkl.get_token")
    def test_simkl_callback_creates_schedule(self, mock_get_token, mock_delay):
        """Test a recurring SIMKL import stores the encrypted token in the schedule."""
        mock_get_token.return_value = {
            "access_token": "simkl-access",
            "username": "simkl-user",
        }
        self.set_oauth_state("state-token", "daily")

        response = self.client.get(
            reverse("import_simkl_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_delay.assert_not_called()
        periodic_task = PeriodicTask.objects.get(task="Import from SIMKL")
        task_kwargs = json.loads(periodic_task.kwargs)
        self.assertEqual(task_kwargs["username"], "simkl-user")
        self.assertEqual(task_kwargs["user_id"], self.user.id)
        self.assertEqual(helpers.decrypt(task_kwargs["token"]), "simkl-access")

    @patch("integrations.views.tasks.import_anilist.delay")
    @patch("integrations.views.anilist.get_token")
    def test_anilist_callback_queues_one_time_import(self, mock_get_token, mock_delay):
        """Test a one-time AniList import is queued with an encrypted token."""
        mock_get_token.return_value = {
            "access_token": "anilist-access",
            "username": "anilist-user",
        }
        self.set_oauth_state("state-token", "once")

        response = self.client.get(
            reverse("import_anilist_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["username"], "anilist-user")
        self.assertEqual(helpers.decrypt(kwargs["token"]), "anilist-access")

    @patch("integrations.views.tasks.import_anilist.delay")
    @patch("integrations.views.anilist.get_token")
    def test_anilist_callback_creates_schedule(self, mock_get_token, mock_delay):
        """Test a recurring AniList import stores the token in the schedule."""
        mock_get_token.return_value = {
            "access_token": "anilist-access",
            "username": "anilist-user",
        }
        self.set_oauth_state("state-token", "daily")

        self.client.get(
            reverse("import_anilist_private"),
            {"code": "code", "state": "state-token"},
        )

        mock_delay.assert_not_called()
        periodic_task = PeriodicTask.objects.get(task="Import from AniList")
        task_kwargs = json.loads(periodic_task.kwargs)
        self.assertEqual(task_kwargs["username"], "anilist-user")
        self.assertEqual(helpers.decrypt(task_kwargs["token"]), "anilist-access")

    @patch("integrations.views.tasks.import_anilist.delay")
    @patch("integrations.views.anilist.get_token")
    def test_anilist_callback_requires_username(self, mock_get_token, mock_delay):
        """Test nothing is imported when AniList returns no username."""
        mock_get_token.return_value = {"access_token": "anilist-access", "username": ""}
        self.set_oauth_state("state-token", "once")

        response = self.client.get(
            reverse("import_anilist_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        self.assert_message(response, "AniList username is required.")
        mock_delay.assert_not_called()
        self.assertFalse(PeriodicTask.objects.exists())

    def test_callbacks_reject_missing_or_unknown_state(self):
        """Test a callback without a state we issued never exchanges a token."""
        cases = [
            ("import_simkl_private", "simkl.get_token", "SIMKL"),
            ("import_anilist_private", "anilist.get_token", "AniList"),
            ("import_trakt_private", "trakt.handle_oauth_callback", "Trakt"),
        ]
        for url_name, exchange, label in cases:
            for query in ({"code": "code"}, {"code": "code", "state": "unknown"}):
                with (
                    self.subTest(url_name=url_name, query=query),
                    patch(f"integrations.views.{exchange}") as mock_exchange,
                ):
                    response = self.client.get(reverse(url_name), query)

                    self.assertRedirects(response, reverse("import_data"))
                    self.assert_message(
                        response,
                        f"Invalid or expired {label} authorization request.",
                    )
                    mock_exchange.assert_not_called()

    def test_callbacks_require_code(self):
        """Test a denied authorization (no code) never exchanges a token."""
        cases = [
            ("import_simkl_private", "simkl.get_token", "SIMKL"),
            ("import_anilist_private", "anilist.get_token", "AniList"),
        ]
        self.set_oauth_state("state-token", "once")
        for url_name, exchange, label in cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.{exchange}") as mock_exchange,
            ):
                response = self.client.get(
                    reverse(url_name),
                    {"state": "state-token", "error": "access_denied"},
                )

                self.assertRedirects(response, reverse("import_data"))
                self.assert_message(response, f"{label} authorization failed.")
                mock_exchange.assert_not_called()

    def test_callbacks_explain_rejected_credentials(self):
        """Test a bad secret key is shown to the user instead of a server error."""
        cases = [
            ("import_simkl_private", "simkl.get_token", "import_simkl"),
            ("import_anilist_private", "anilist.get_token", "import_anilist"),
            ("import_trakt_private", "trakt.handle_oauth_callback", "import_trakt"),
        ]
        self.set_oauth_state("state-token", "once")
        for url_name, exchange, task_name in cases:
            with (
                self.subTest(url_name=url_name),
                patch(f"integrations.views.{exchange}") as mock_exchange,
                patch(f"integrations.views.tasks.{task_name}.delay") as mock_delay,
            ):
                mock_exchange.side_effect = helpers.MediaImportError(
                    "Invalid secret key.",
                )

                response = self.client.get(
                    reverse(url_name),
                    {"code": "code", "state": "state-token"},
                )

                self.assertRedirects(response, reverse("import_data"))
                self.assert_message(response, "Invalid secret key.")
                mock_delay.assert_not_called()
        self.assertFalse(PeriodicTask.objects.exists())

    @patch("integrations.views.tasks.import_anilist.delay")
    @patch("integrations.views.tasks.import_simkl.delay")
    @patch("integrations.views.anilist.get_token")
    @patch("integrations.views.simkl.get_token")
    def test_state_is_single_use(self, mock_simkl, mock_anilist, *_):
        """Test a used state cannot be replayed."""
        token = {"access_token": "access", "username": "someone"}
        mock_simkl.return_value = token
        mock_anilist.return_value = token
        for url_name in ("import_simkl_private", "import_anilist_private"):
            with self.subTest(url_name=url_name):
                self.set_oauth_state("state-token", "once")

                self.client.get(
                    reverse(url_name),
                    {"code": "code", "state": "state-token"},
                )

                self.assertNotIn("state-token", self.client.session)

    @patch("integrations.views.trakt.handle_oauth_callback")
    def test_trakt_callback_requires_code(self, mock_oauth_callback):
        """Test a Trakt callback without a code never exchanges a token."""
        self.set_oauth_state("state-token", "once")

        response = self.client.get(
            reverse("import_trakt_private"),
            {"state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        self.assert_message(response, "Trakt authorization failed.")
        mock_oauth_callback.assert_not_called()

    @patch("integrations.views.tasks.import_trakt.delay")
    @patch("integrations.views.trakt.handle_oauth_callback")
    def test_trakt_callback_creates_schedule(self, mock_oauth_callback, mock_delay):
        """Test a recurring Trakt import stores the token and redirect URI."""
        redirect_uri = "https://mytrackz.example.com/import/trakt/private"
        mock_oauth_callback.return_value = {
            "refresh_token": "trakt-refresh",
            "username": "trakt-user",
        }
        self.set_oauth_state("state-token", "daily", redirect_uri=redirect_uri)

        response = self.client.get(
            reverse("import_trakt_private"),
            {"code": "code", "state": "state-token"},
        )

        self.assertRedirects(response, reverse("import_data"))
        mock_delay.assert_not_called()
        periodic_task = PeriodicTask.objects.get(task="Import from Trakt")
        task_kwargs = json.loads(periodic_task.kwargs)
        self.assertEqual(task_kwargs["username"], "trakt-user")
        self.assertEqual(task_kwargs["redirect_uri"], redirect_uri)
        self.assertEqual(helpers.decrypt(task_kwargs["token"]), "trakt-refresh")
        # The state is single use.
        self.assertNotIn("state-token", self.client.session)


class ExportViewTests(ImportViewTestCase):
    """Test the CSV export view."""

    def test_export_csv_streams_attachment(self):
        """Test the export is served as a CSV download."""
        response = self.client.get(reverse("export_csv"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(
            'attachment; filename="mytrackz_', response["Content-Disposition"]
        )
        self.assertTrue(b"".join(response.streaming_content))

    def test_export_csv_requires_login(self):
        """Test the export is not served to anonymous visitors."""
        self.client.logout()

        response = self.client.get(reverse("export_csv"))

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.get("Content-Type"), "text/csv")


class WebhookPayloadTests(TestCase):
    """Test the media-server webhooks reject requests without a payload."""

    def setUp(self):
        """Create a user with a known webhook token."""
        credentials = {"username": "testuser", "token": "test-token"}
        self.user = get_user_model().objects.create_user(**credentials)

    def test_jellyfin_missing_payload(self):
        """Test an empty Jellyfin request is a bad request."""
        response = self.client.post(
            reverse("jellyfin_webhook", kwargs={"token": "test-token"}),
            data="",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_plex_missing_payload(self):
        """Test a Plex request without the payload field is a bad request."""
        response = self.client.post(
            reverse("plex_webhook", kwargs={"token": "test-token"}),
            {"other": "value"},
        )
        self.assertEqual(response.status_code, 400)

    def test_emby_missing_payload(self):
        """Test an Emby request without the data field is a bad request."""
        response = self.client.post(
            reverse("emby_webhook", kwargs={"token": "test-token"}),
            {"other": "value"},
        )
        self.assertEqual(response.status_code, 400)

    def test_webhooks_reject_unknown_token(self):
        """Test every webhook refuses a token that belongs to no user."""
        for url_name in ("jellyfin_webhook", "plex_webhook", "emby_webhook"):
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name, kwargs={"token": "wrong-token"}),
                )
                self.assertEqual(response.status_code, 401)
