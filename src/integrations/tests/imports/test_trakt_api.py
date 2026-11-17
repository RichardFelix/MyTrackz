import json
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from app.models import TV, Item, MediaTypes, Movie, Season, Sources, Status
from app.providers import services
from integrations.imports import helpers, trakt
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError
from integrations.imports.trakt import TraktImporter


def http_error(status_code):
    """Return the error the request helper raises for an HTTP status."""
    response = Mock(status_code=status_code, text="error")
    return requests.exceptions.HTTPError(response=response)


def provider_error(status_code):
    """Return the error the metadata layer raises for an HTTP status."""
    return services.ProviderAPIError(Sources.TMDB.value, http_error(status_code))


def tv_metadata(media_type, _tmdb_id, _title, _season_number=None):
    """Return metadata for a one-season show with two episodes."""
    if media_type == MediaTypes.SEASON.value:
        return {
            "title": "Season 1",
            "image": "season.jpg",
            "max_progress": 2,
            "episodes": [
                {"episode_number": 1, "still_path": "/still.jpg"},
                {"episode_number": 2, "still_path": None},
            ],
        }
    return {"title": "Test Show", "image": "tv.jpg", "last_episode_season": 1}


def episode_entry(number, watched_at="2023-01-01T00:00:00.000Z"):
    """Return a Trakt history entry for an episode of the test show."""
    return {
        "type": "episode",
        "episode": {"season": 1, "number": number},
        "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
        "watched_at": watched_at,
    }


class TraktOAuthTests(TestCase):
    """Test the Trakt token exchange and token refresh."""

    @override_settings(TRAKT_API="client", TRAKT_API_SECRET="secret")  # noqa: S106
    @patch("app.providers.services.api_request")
    def test_handle_oauth_callback(self, mock_api_request):
        """Test the code is exchanged and the username looked up."""
        mock_api_request.side_effect = [
            {"refresh_token": "refresh", "access_token": "access"},
            {"username": "trakt-user"},
        ]
        request = RequestFactory().get("/import/trakt/private", {"code": "abc"})

        result = trakt.handle_oauth_callback(
            request,
            redirect_uri="https://mytrackz.example.com/import/trakt/private",
        )

        self.assertEqual(
            result,
            {"refresh_token": "refresh", "username": "trakt-user"},
        )
        token_call, user_call = mock_api_request.call_args_list
        self.assertEqual(token_call.args[1], "POST")
        self.assertEqual(
            token_call.kwargs["params"],
            {
                "client_id": "client",
                "client_secret": "secret",
                "code": "abc",
                "grant_type": "authorization_code",
                "redirect_uri": "https://mytrackz.example.com/import/trakt/private",
            },
        )
        self.assertEqual(user_call.args[2], "https://api.trakt.tv/users/me")
        self.assertEqual(user_call.kwargs["headers"]["Authorization"], "Bearer access")

    @patch("app.providers.services.api_request")
    def test_handle_oauth_callback_builds_redirect_uri(self, mock_api_request):
        """Test the callback URL is derived from the request when not supplied."""
        mock_api_request.side_effect = [
            {"refresh_token": "refresh", "access_token": "access"},
            {"username": "trakt-user"},
        ]
        request = RequestFactory().get("/import/trakt/private", {"code": "abc"})

        trakt.handle_oauth_callback(request)

        redirect_uri = mock_api_request.call_args_list[0].kwargs["params"][
            "redirect_uri"
        ]
        self.assertTrue(redirect_uri.endswith("/import/trakt/private"))

    @patch("app.providers.services.api_request")
    def test_get_access_token_rotates_stored_refresh_token(self, mock_api_request):
        """Test a scheduled import keeps working after its token is used.

        Trakt refresh tokens are single use, so the schedule must be updated
        with the replacement or the next run would fail.
        """
        old_token = helpers.encrypt("old-refresh")
        crontab = CrontabSchedule.objects.create(hour=3, minute=0)
        periodic_task = PeriodicTask.objects.create(
            name="Import from Trakt for someone",
            task="Import from Trakt",
            crontab=crontab,
            kwargs=json.dumps({"username": "someone", "token": old_token}),
        )
        other_task = PeriodicTask.objects.create(
            name="Import from Trakt for someone else",
            task="Import from Trakt",
            crontab=crontab,
            kwargs=json.dumps({"username": "other", "token": "untouched"}),
        )
        mock_api_request.return_value = {
            "refresh_token": "new-refresh",
            "access_token": "new-access",
        }

        access_token = trakt.get_access_token(old_token, redirect_uri="https://x/cb")

        self.assertEqual(access_token, "new-access")
        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["refresh_token"], "old-refresh")
        self.assertEqual(params["grant_type"], "refresh_token")

        periodic_task.refresh_from_db()
        task_kwargs = json.loads(periodic_task.kwargs)
        self.assertEqual(helpers.decrypt(task_kwargs["token"]), "new-refresh")
        self.assertEqual(task_kwargs["username"], "someone")

        other_task.refresh_from_db()
        self.assertEqual(json.loads(other_task.kwargs)["token"], "untouched")

    @patch("app.providers.services.api_request")
    def test_rejected_credentials_are_explained(self, mock_api_request):
        """Test a 401 from Trakt is reported as a bad secret key."""
        mock_api_request.side_effect = http_error(401)
        request = RequestFactory().get("/import/trakt/private", {"code": "abc"})

        with self.assertRaisesMessage(MediaImportError, "Invalid Trakt secret key."):
            trakt.handle_oauth_callback(request, redirect_uri="https://x/cb")
        with self.assertRaisesMessage(MediaImportError, "Invalid Trakt secret key."):
            trakt.get_username_from_oauth("access")
        with self.assertRaisesMessage(MediaImportError, "Invalid Trakt secret key."):
            trakt.get_access_token(helpers.encrypt("refresh"), redirect_uri="https://x")

    @patch("app.providers.services.api_request")
    def test_other_oauth_errors_propagate(self, mock_api_request):
        """Test a Trakt outage is not blamed on the secret key."""
        mock_api_request.side_effect = http_error(503)
        request = RequestFactory().get("/import/trakt/private", {"code": "abc"})

        with self.assertRaises(requests.exceptions.HTTPError):
            trakt.handle_oauth_callback(request, redirect_uri="https://x/cb")
        with self.assertRaises(requests.exceptions.HTTPError):
            trakt.get_username_from_oauth("access")
        with self.assertRaises(requests.exceptions.HTTPError):
            trakt.get_access_token(helpers.encrypt("refresh"), redirect_uri="https://x")

    def test_update_refresh_token_without_schedule(self):
        """Test a one-time import has no schedule to update."""
        trakt.update_refresh_token("missing", "new-refresh")

        self.assertFalse(PeriodicTask.objects.exists())


class TraktRequestTests(TestCase):
    """Test how the importer talks to the Trakt API."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    @patch("integrations.imports.trakt.get_access_token")
    @patch("app.providers.services.api_request")
    def test_public_request_sends_no_authorization(
        self,
        mock_api_request,
        mock_get_access_token,
    ):
        """Test a public import never asks for or sends an access token."""
        importer = TraktImporter("someone", self.user, "new")

        importer._make_api_request("https://api.trakt.tv/users/someone/watchlist")

        headers = mock_api_request.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["trakt-api-version"], "2")
        mock_get_access_token.assert_not_called()

    @patch("integrations.imports.trakt.get_access_token")
    @patch("app.providers.services.api_request")
    def test_oauth_request_fetches_access_token_once(
        self,
        mock_api_request,
        mock_get_access_token,
    ):
        """Test the single-use refresh token is only spent on the first request."""
        mock_get_access_token.return_value = "access"
        importer = TraktImporter(
            "someone",
            self.user,
            "new",
            refresh_token="encrypted",  # noqa: S106
            redirect_uri="https://x/cb",
        )

        importer._make_api_request("https://api.trakt.tv/users/someone/watchlist")
        importer._make_api_request("https://api.trakt.tv/users/someone/ratings")

        mock_get_access_token.assert_called_once_with(
            "encrypted",
            redirect_uri="https://x/cb",
        )
        for call in mock_api_request.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer access")

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_paginated_data_reads_until_empty_page(self, mock_make_request):
        """Test every page is collected, in order."""
        mock_make_request.side_effect = [[1, 2], [3], []]
        importer = TraktImporter("someone", self.user, "new")

        data = importer._get_paginated_data(
            "https://api.trakt.tv/users/someone/history"
        )

        self.assertEqual(data, [1, 2, 3])
        urls = [call.args[0] for call in mock_make_request.call_args_list]
        self.assertEqual(
            urls,
            [
                f"https://api.trakt.tv/users/someone/history?page={page}&limit=1000"
                for page in (1, 2, 3)
            ],
        )

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_paginated_data_unknown_user(self, mock_make_request):
        """Test an unknown user slug is reported in plain words."""
        mock_make_request.side_effect = http_error(404)
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaisesMessage(MediaImportError, "User slug someone not found"):
            importer._get_paginated_data("https://api.trakt.tv/users/someone/history")

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_paginated_data_private_account(self, mock_make_request):
        """Test a private account points the user at the OAuth import."""
        mock_make_request.side_effect = http_error(401)
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaisesMessage(MediaImportError, "use OAuth import instead"):
            importer._get_paginated_data("https://api.trakt.tv/users/someone/history")

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_paginated_data_other_errors_propagate(self, mock_make_request):
        """Test an unexpected HTTP error is not disguised as a user mistake."""
        mock_make_request.side_effect = http_error(500)
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaises(requests.exceptions.HTTPError):
            importer._get_paginated_data("https://api.trakt.tv/users/someone/history")


class TraktProcessingTests(TestCase):
    """Test how Trakt entries become media, beyond the happy path."""

    def setUp(self):
        """Create user for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def create_existing_movie(self, tmdb_id="67890"):
        """Track a movie for the user before the import runs."""
        item = Item.objects.create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Existing Movie",
            image="http://example.com/image.jpg",
        )
        return Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.PLANNING.value,
        )

    def movie_entry(self, tmdb_id=67890):
        """Return a Trakt history entry for a movie."""
        return {
            "type": "movie",
            "movie": {"title": "Test Movie", "ids": {"tmdb": tmdb_id}},
            "watched_at": "2023-01-02T00:00:00.000Z",
        }

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_movie_without_tmdb_id_is_skipped_with_warning(self, mock_get_metadata):
        """Test an entry Trakt cannot map to TMDB is reported, not imported."""
        importer = TraktImporter("someone", self.user, "new")

        importer.process_watched_movie(self.movie_entry(tmdb_id=None))
        importer.process_watched_movie(
            {"movie": {"title": "No IDs"}, "watched_at": "2023-01-02T00:00:00.000Z"},
        )

        self.assertEqual(importer.bulk_media[MediaTypes.MOVIE.value], [])
        self.assertEqual(
            importer.warnings,
            [
                "Test Movie: No The Movie Database ID found.",
                "No IDs: No The Movie Database ID found.",
            ],
        )
        mock_get_metadata.assert_not_called()

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_new_mode_skips_existing_movie(self, mock_get_metadata):
        """Test "new" mode leaves media the user already tracks alone."""
        self.create_existing_movie()
        importer = TraktImporter("someone", self.user, "new")

        importer.process_watched_movie(self.movie_entry())

        self.assertEqual(importer.bulk_media[MediaTypes.MOVIE.value], [])
        self.assertFalse(importer.to_delete[MediaTypes.MOVIE.value])
        mock_get_metadata.assert_not_called()

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_overwrite_mode_replaces_existing_movie(self, mock_get_metadata):
        """Test "overwrite" mode queues the old row for deletion and re-imports."""
        self.create_existing_movie()
        mock_get_metadata.return_value = {"title": "Test Movie", "image": "movie.jpg"}
        importer = TraktImporter("someone", self.user, "overwrite")

        importer.process_watched_movie(self.movie_entry())

        self.assertEqual(len(importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        self.assertEqual(
            importer.to_delete[MediaTypes.MOVIE.value][Sources.TMDB.value],
            {"67890"},
        )

    @patch("app.providers.services.get_media_metadata")
    def test_metadata_not_found_becomes_warning(self, mock_get_media_metadata):
        """Test a title missing from TMDB is skipped with a warning."""
        mock_get_media_metadata.side_effect = provider_error(404)
        importer = TraktImporter("someone", self.user, "new")

        movie = importer._get_metadata(MediaTypes.MOVIE.value, "1", "Gone Movie")
        season = importer._get_metadata(MediaTypes.SEASON.value, "2", "Gone Show", 3)

        self.assertIsNone(movie)
        self.assertIsNone(season)
        self.assertEqual(
            importer.warnings,
            [
                "Gone Movie: not found in The Movie Database with ID 1.",
                "Gone Show S3: not found in The Movie Database with ID 2.",
            ],
        )
        self.assertEqual(
            mock_get_media_metadata.call_args.kwargs,
            {"season_numbers": [3]},
        )

    @patch("app.providers.services.get_media_metadata")
    def test_metadata_other_errors_propagate(self, mock_get_media_metadata):
        """Test a provider outage is not mistaken for a missing title."""
        mock_get_media_metadata.side_effect = provider_error(500)
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaises(services.ProviderAPIError):
            importer._get_metadata(MediaTypes.MOVIE.value, "1", "Movie")
        self.assertEqual(importer.warnings, [])

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_episode_missing_from_tmdb_is_skipped(self, mock_get_metadata):
        """Test an episode number TMDB does not list is reported, not imported."""
        mock_get_metadata.side_effect = tv_metadata
        importer = TraktImporter("someone", self.user, "new")

        importer.process_watched_episode(episode_entry(9))

        self.assertEqual(importer.bulk_media[MediaTypes.EPISODE.value], [])
        self.assertEqual(importer.bulk_media[MediaTypes.TV.value], [])
        self.assertEqual(
            importer.warnings,
            ["Test Show S1E9: not found in The Movie Database with ID 12345."],
        )

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_episode_without_show_metadata_is_skipped(self, mock_get_metadata):
        """Test nothing is created when the show or season is missing from TMDB."""
        importer = TraktImporter("someone", self.user, "new")

        mock_get_metadata.return_value = None
        importer.process_watched_episode(episode_entry(1))

        mock_get_metadata.side_effect = lambda media_type, *_: (
            None if media_type == MediaTypes.SEASON.value else {"title": "Test Show"}
        )
        importer.process_watched_episode(episode_entry(1))

        self.assertFalse(any(importer.bulk_media.values()))
        self.assertFalse(Item.objects.exists())

    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_episodes_share_show_and_complete_it(self, mock_get_metadata):
        """Test episodes of one show reuse its rows and the finale completes it."""
        mock_get_metadata.side_effect = tv_metadata
        importer = TraktImporter("someone", self.user, "new")

        importer.process_watched_episode(episode_entry(1))

        tv_obj = importer.bulk_media[MediaTypes.TV.value][0]
        season_obj = importer.bulk_media[MediaTypes.SEASON.value][0]
        self.assertEqual(tv_obj.status, Status.IN_PROGRESS.value)
        self.assertEqual(season_obj.status, Status.IN_PROGRESS.value)

        importer.process_watched_episode(episode_entry(2))

        self.assertEqual(len(importer.bulk_media[MediaTypes.TV.value]), 1)
        self.assertEqual(len(importer.bulk_media[MediaTypes.SEASON.value]), 1)
        episodes = importer.bulk_media[MediaTypes.EPISODE.value]
        self.assertEqual(len(episodes), 2)
        self.assertIs(episodes[1].related_season, season_obj)
        self.assertIs(season_obj.related_tv, tv_obj)
        self.assertEqual(season_obj.status, Status.COMPLETED.value)
        self.assertEqual(tv_obj.status, Status.COMPLETED.value)

        # An episode with a still uses it; one without falls back to the placeholder.
        self.assertEqual(
            episodes[0].item.image,
            "https://image.tmdb.org/t/p/w500/still.jpg",
        )
        self.assertEqual(episodes[1].item.image, settings.IMG_NONE)

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    def test_history_is_processed_oldest_first(self, mock_get_paginated_data):
        """Test Trakt's newest-first history is replayed chronologically."""
        mock_get_paginated_data.return_value = [
            {**self.movie_entry(2), "watched_at": "2023-02-01T00:00:00.000Z"},
            {**self.movie_entry(1), "watched_at": "2023-01-01T00:00:00.000Z"},
        ]
        importer = TraktImporter("someone", self.user, "new")

        with (
            patch.object(importer, "process_watched_movie") as mock_movie,
            patch.object(importer, "process_watched_episode") as mock_episode,
        ):
            importer.process_history()

        watched = [call.args[0]["watched_at"] for call in mock_movie.call_args_list]
        self.assertEqual(
            watched,
            ["2023-01-01T00:00:00.000Z", "2023-02-01T00:00:00.000Z"],
        )
        mock_episode.assert_not_called()

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    def test_history_error_names_the_entry(self, mock_get_paginated_data):
        """Test a malformed entry is reported with the entry that caused it."""
        broken_entry = {"type": "movie", "watched_at": "2023-01-01T00:00:00.000Z"}
        mock_get_paginated_data.return_value = [broken_entry]
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing history entry",
        ):
            importer.process_history()

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    def test_watchlist_and_rating_errors_name_the_entry(self, mock_make_request):
        """Test malformed watchlist and rating entries are reported clearly."""
        mock_make_request.return_value = [{"type": "movie", "rating": 5}]
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing watchlist entry",
        ):
            importer.process_watchlist()
        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing rating entry",
        ):
            importer.process_ratings()

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    def test_comment_errors_name_the_entry(self, mock_get_paginated_data):
        """Test a malformed comment entry is reported clearly."""
        mock_get_paginated_data.return_value = [
            {"type": "movie", "comment": {"comment": "Great"}},
        ]
        importer = TraktImporter("someone", self.user, "new")

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing comment entry",
        ):
            importer.process_comments()

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_season_rating_creates_parent_show(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test rating a season also tracks its show, linked together."""
        mock_get_metadata.side_effect = tv_metadata
        mock_make_request.return_value = [
            {
                "rated_at": "2023-01-01T00:00:00.000Z",
                "type": "season",
                "season": {"number": 1},
                "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
                "rating": 9,
            },
        ]
        importer = TraktImporter("someone", self.user, "new")

        importer.process_ratings()

        tv_obj = importer.bulk_media[MediaTypes.TV.value][0]
        season_obj = importer.bulk_media[MediaTypes.SEASON.value][0]
        self.assertIsInstance(tv_obj, TV)
        self.assertIsInstance(season_obj, Season)
        self.assertEqual(season_obj.score, 9)
        self.assertIs(season_obj.related_tv, tv_obj)
        self.assertEqual(season_obj.item.season_number, 1)
        self.assertEqual(tv_obj.status, Status.IN_PROGRESS.value)

    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_season_rating_without_show_metadata_is_skipped(
        self,
        mock_get_metadata,
        mock_make_request,
    ):
        """Test a rated season is dropped when its show is missing from TMDB."""
        mock_get_metadata.side_effect = lambda media_type, *_: (
            {"title": "Season 1", "image": "season.jpg"}
            if media_type == MediaTypes.SEASON.value
            else None
        )
        mock_make_request.return_value = [
            {
                "rated_at": "2023-01-01T00:00:00.000Z",
                "type": "season",
                "season": {"number": 1},
                "show": {"title": "Test Show", "ids": {"tmdb": 12345}},
                "rating": 9,
            },
        ]
        importer = TraktImporter("someone", self.user, "new")

        importer.process_ratings()

        self.assertFalse(any(importer.bulk_media.values()))

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_rating_and_comment_merge_into_watched_movie(
        self,
        mock_get_metadata,
        mock_make_request,
        mock_get_paginated_data,
    ):
        """Test a rating and a comment update every watch of the same movie."""
        mock_get_metadata.return_value = {"title": "Test Movie", "image": "movie.jpg"}
        importer = TraktImporter("someone", self.user, "new")
        importer.process_watched_movie(self.movie_entry())
        importer.process_watched_movie(self.movie_entry())

        mock_make_request.return_value = [
            {
                "rated_at": "2023-01-03T00:00:00.000Z",
                "type": "movie",
                "movie": {"title": "Test Movie", "ids": {"tmdb": 67890}},
                "rating": 7,
            },
        ]
        importer.process_ratings()
        mock_get_paginated_data.return_value = [
            {
                "type": "movie",
                "movie": {"title": "Test Movie", "ids": {"tmdb": 67890}},
                "comment": {
                    "comment": "Worth a rewatch",
                    "updated_at": "2023-01-04T00:00:00.000Z",
                },
            },
        ]
        importer.process_comments()

        movies = importer.bulk_media[MediaTypes.MOVIE.value]
        self.assertEqual(len(movies), 2)
        for movie in movies:
            self.assertEqual(movie.score, 7)
            self.assertEqual(movie.notes, "Worth a rewatch")
            self.assertEqual(movie.status, Status.COMPLETED.value)

    @patch("integrations.imports.trakt.TraktImporter._get_paginated_data")
    @patch("integrations.imports.trakt.TraktImporter._make_api_request")
    @patch("integrations.imports.trakt.TraktImporter._get_metadata")
    def test_import_data_saves_media_and_deduplicates_warnings(
        self,
        mock_get_metadata,
        mock_make_request,
        mock_get_paginated_data,
    ):
        """Test a full import writes to the library and reports each warning once."""
        mock_get_metadata.return_value = {"title": "Test Movie", "image": "movie.jpg"}
        no_id_entry = {
            "type": "movie",
            "movie": {"title": "No IDs", "ids": {}},
            "watched_at": "2023-01-01T00:00:00.000Z",
        }
        history = [self.movie_entry(), no_id_entry, no_id_entry]
        mock_get_paginated_data.side_effect = [history, []]
        mock_make_request.return_value = []

        imported_counts, warnings = trakt.importer(None, self.user, "new", "someone")

        self.assertEqual(imported_counts, {MediaTypes.MOVIE.value: 1})
        self.assertEqual(warnings, "No IDs: No The Movie Database ID found.")
        movie = Movie.objects.get(user=self.user)
        self.assertEqual(movie.item.media_id, "67890")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.progress, 1)
