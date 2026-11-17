from datetime import UTC, datetime
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from app.models import Anime, Item, MediaTypes, Movie, Sources, Status
from app.providers import services
from integrations.imports import helpers, simkl
from integrations.imports.helpers import MediaImportUnexpectedError


def provider_error(source, status_code):
    """Return the error the metadata layer raises for an HTTP status."""
    response = Mock(status_code=status_code, text="error")
    error = requests.exceptions.HTTPError(response=response)
    return services.ProviderAPIError(source, error)


def movie_entry(tmdb_id=603, **overrides):
    """Return a SIMKL movie entry."""
    entry = {
        "last_watched_at": "2023-03-01T10:00:00Z",
        "movie": {"title": "The Matrix", "ids": {"tmdb": tmdb_id}},
        "status": "completed",
        "user_rating": 9,
        "memo": {},
    }
    entry.update(overrides)
    return entry


def anime_entry(mal_id=1, **overrides):
    """Return a SIMKL anime entry."""
    entry = {
        "last_watched_at": "2023-03-05T10:00:00Z",
        "show": {"title": "Cowboy Bebop", "ids": {"mal": mal_id}},
        "status": "completed",
        "user_rating": 10,
        "watched_episodes_count": 26,
        "memo": {"text": "Classic"},
        "seasons": [
            {
                "episodes": [
                    {"number": 1, "watched_at": "2023-02-10T10:00:00Z"},
                    {"number": 2},
                    {"number": 3, "watched_at": "2023-02-01T10:00:00Z"},
                ],
            },
        ],
    }
    entry.update(overrides)
    return entry


def tv_entry(tmdb_id=1396, **overrides):
    """Return a SIMKL TV entry."""
    entry = {
        "last_watched_at": "2023-01-02T00:00:00Z",
        "show": {"title": "Breaking Bad", "ids": {"tmdb": tmdb_id}},
        "status": "watching",
        "user_rating": 8,
        "memo": {},
    }
    entry.update(overrides)
    return entry


class SimklOAuthTests(TestCase):
    """Test the SIMKL token exchange."""

    @override_settings(SIMKL_ID="client", SIMKL_SECRET="secret")  # noqa: S106
    @patch("app.providers.services.api_request")
    def test_get_token(self, mock_api_request):
        """Test the code is exchanged and the username looked up."""
        mock_api_request.side_effect = [
            {"access_token": "access"},
            {"user": {"name": "simkl-user"}},
        ]
        request = RequestFactory().get("/import/simkl_private", {"code": "abc"})

        result = simkl.get_token(request)

        self.assertEqual(result, {"access_token": "access", "username": "simkl-user"})
        token_call, user_call = mock_api_request.call_args_list
        params = token_call.kwargs["params"]
        self.assertEqual(params["client_id"], "client")
        self.assertEqual(params["client_secret"], "secret")
        self.assertEqual(params["code"], "abc")
        self.assertEqual(params["grant_type"], "authorization_code")
        self.assertTrue(params["redirect_uri"].endswith("/import/simkl_private"))
        self.assertEqual(user_call.args[2], "https://api.simkl.com/users/settings")
        self.assertEqual(user_call.kwargs["headers"]["Authorization"], "Bearer access")


class SimklImporterTests(TestCase):
    """Test how SIMKL entries become media, beyond the happy path."""

    def setUp(self):
        """Create user and importer for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.importer = simkl.SimklImporter(
            helpers.encrypt("token"),
            self.user,
            "new",
        )

    @override_settings(SIMKL_ID="client")
    @patch("app.providers.services.api_request")
    def test_get_user_list_sends_decrypted_token(self, mock_api_request):
        """Test the list request carries the decrypted token and asks for dates."""
        mock_api_request.return_value = {"movies": []}

        data = self.importer._get_user_list()

        self.assertEqual(data, {"movies": []})
        kwargs = mock_api_request.call_args.kwargs
        self.assertIn("token", kwargs["headers"]["Authorization"])
        self.assertEqual(kwargs["headers"]["simkl-api-key"], "client")
        self.assertEqual(kwargs["params"]["episode_watched_at"], "yes")
        self.assertEqual(kwargs["params"]["memos"], "yes")

    @patch("integrations.imports.simkl.SimklImporter._get_user_list")
    def test_empty_account_imports_nothing(self, mock_get_user_list):
        """Test an account with no data returns cleanly."""
        mock_get_user_list.return_value = None

        self.assertEqual(self.importer.import_data(), ({}, ""))

    @patch("app.providers.tmdb.movie")
    @patch("integrations.imports.simkl.SimklImporter._get_user_list")
    def test_movies_are_saved_with_status_and_progress(
        self,
        mock_get_user_list,
        mock_movie,
    ):
        """Test movies are written with the right status, progress and notes."""
        mock_movie.side_effect = lambda tmdb_id: {
            "title": f"Movie {tmdb_id}",
            "image": "movie.jpg",
        }
        mock_get_user_list.return_value = {
            "movies": [
                movie_entry(603, memo={"text": "Rewatch soon"}),
                movie_entry(
                    604,
                    status="plantowatch",
                    user_rating=None,
                    last_watched_at=None,
                    added_to_watchlist_at="2023-04-01T10:00:00Z",
                ),
            ],
        }

        imported_counts, warnings = self.importer.import_data()

        self.assertEqual(imported_counts, {MediaTypes.MOVIE.value: 2})
        self.assertEqual(warnings, "")

        watched = Movie.objects.get(user=self.user, item__media_id="603")
        self.assertEqual(watched.status, Status.COMPLETED.value)
        self.assertEqual(watched.progress, 1)
        self.assertEqual(watched.score, 9)
        self.assertEqual(watched.notes, "Rewatch soon")
        self.assertEqual(watched.end_date, datetime(2023, 3, 1, 10, 0, tzinfo=UTC))

        planned = Movie.objects.get(user=self.user, item__media_id="604")
        self.assertEqual(planned.status, Status.PLANNING.value)
        self.assertEqual(planned.progress, 0)
        self.assertIsNone(planned.end_date)
        self.assertEqual(planned.notes, "")

    @patch("app.providers.tmdb.movie")
    def test_movie_problems_become_warnings(self, mock_movie):
        """Test entries that cannot be imported are reported, not fatal."""

        def movie_metadata(tmdb_id):
            if tmdb_id == 404:  # noqa: PLR2004
                raise provider_error(Sources.TMDB.value, 404)
            return {"title": "The Matrix", "image": "movie.jpg"}

        mock_movie.side_effect = movie_metadata

        self.importer._process_movie_list(
            [
                movie_entry(603),
                movie_entry(603),
                {**movie_entry(), "movie": {"title": "No IDs", "ids": {}}},
                movie_entry(404),
            ],
        )

        self.assertEqual(len(self.importer.bulk_media[MediaTypes.MOVIE.value]), 1)
        self.assertEqual(
            self.importer.warnings,
            [
                "The Matrix (603) already present in the import list",
                "No IDs: No TMDB ID found",
                f"The Matrix: not found in {Sources.TMDB.label} with ID 404.",
            ],
        )

    @patch("app.providers.tmdb.movie")
    def test_movie_provider_outage_stops_import(self, mock_movie):
        """Test a provider failure other than "not found" aborts with the entry."""
        mock_movie.side_effect = provider_error(Sources.TMDB.value, 500)

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing entry",
        ):
            self.importer._process_movie_list([movie_entry()])

    @patch("app.providers.tmdb.movie")
    def test_new_mode_skips_existing_movie(self, mock_movie):
        """Test "new" mode leaves a movie the user already tracks alone."""
        item = Item.objects.create(
            media_id="603",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(item=item, user=self.user, status=Status.PLANNING.value)
        importer = simkl.SimklImporter(helpers.encrypt("token"), self.user, "new")

        importer._process_movie_list([movie_entry(603)])

        self.assertEqual(importer.bulk_media[MediaTypes.MOVIE.value], [])
        mock_movie.assert_not_called()

    @patch("app.providers.mal.anime")
    @patch("integrations.imports.simkl.SimklImporter._get_user_list")
    def test_anime_dates_come_from_watched_episodes(
        self,
        mock_get_user_list,
        mock_anime,
    ):
        """Test the start date is the earliest watch and only finished anime end."""
        mock_anime.side_effect = lambda mal_id: {
            "title": f"Anime {mal_id}",
            "image": "anime.jpg",
        }
        mock_get_user_list.return_value = {
            "anime": [
                anime_entry(1),
                anime_entry(2, status="watching", watched_episodes_count=3, memo={}),
                {
                    key: value
                    for key, value in anime_entry(3, status="hold").items()
                    if key != "seasons"
                },
            ],
        }

        imported_counts, _ = self.importer.import_data()

        self.assertEqual(imported_counts, {MediaTypes.ANIME.value: 3})

        completed = Anime.objects.get(user=self.user, item__media_id="1")
        self.assertEqual(completed.status, Status.COMPLETED.value)
        self.assertEqual(completed.progress, 26)
        self.assertEqual(completed.notes, "Classic")
        self.assertEqual(completed.start_date, datetime(2023, 2, 1, 10, 0, tzinfo=UTC))
        self.assertEqual(completed.end_date, datetime(2023, 3, 5, 10, 0, tzinfo=UTC))

        watching = Anime.objects.get(user=self.user, item__media_id="2")
        self.assertEqual(watching.status, Status.IN_PROGRESS.value)
        self.assertEqual(watching.progress, 3)
        self.assertIsNone(watching.end_date)

        paused = Anime.objects.get(user=self.user, item__media_id="3")
        self.assertEqual(paused.status, Status.PAUSED.value)
        self.assertIsNone(paused.start_date)

    @patch("app.providers.mal.anime")
    def test_anime_problems_become_warnings(self, mock_anime):
        """Test anime that cannot be imported are reported, not fatal."""

        def anime_metadata(mal_id):
            if mal_id == 404:  # noqa: PLR2004
                raise provider_error(Sources.MAL.value, 404)
            return {"title": "Cowboy Bebop", "image": "anime.jpg"}

        mock_anime.side_effect = anime_metadata

        self.importer._process_anime_list(
            [
                anime_entry(1),
                anime_entry(1),
                {**anime_entry(), "show": {"title": "No IDs", "ids": {}}},
                anime_entry(404),
            ],
        )

        self.assertEqual(len(self.importer.bulk_media[MediaTypes.ANIME.value]), 1)
        self.assertEqual(
            self.importer.warnings,
            [
                "Cowboy Bebop (1) already present in the import list",
                "No IDs: No MyAnimeList ID found",
                f"Cowboy Bebop: not found in {Sources.MAL.label} with ID 404.",
            ],
        )

    @patch("app.providers.mal.anime")
    def test_anime_provider_outage_stops_import(self, mock_anime):
        """Test a provider failure other than "not found" aborts with the entry."""
        mock_anime.side_effect = provider_error(Sources.MAL.value, 500)

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing entry",
        ):
            self.importer._process_anime_list([anime_entry()])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_tv_problems_become_warnings(self, mock_tv_with_seasons):
        """Test shows that cannot be imported are reported, not fatal."""

        def tv_metadata(tmdb_id, _season_numbers):
            if tmdb_id == 404:  # noqa: PLR2004
                raise provider_error(Sources.TMDB.value, 404)
            return {"title": "Breaking Bad", "image": "tv.jpg"}

        mock_tv_with_seasons.side_effect = tv_metadata

        self.importer._process_tv_list(
            [
                tv_entry(1396),
                tv_entry(1396),
                {**tv_entry(), "show": {"title": "No IDs", "ids": {}}},
                tv_entry(404),
            ],
        )

        self.assertEqual(len(self.importer.bulk_media[MediaTypes.TV.value]), 1)
        self.assertEqual(
            self.importer.warnings,
            [
                "Breaking Bad (1396) already present in the import list",
                "No IDs: No TMDB ID found",
                f"Breaking Bad: not found in {Sources.TMDB.label} with ID 404.",
            ],
        )

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_tv_without_seasons_imports_show_only(self, mock_tv_with_seasons):
        """Test a show with no watched seasons is tracked without seasons."""
        mock_tv_with_seasons.return_value = {"title": "Breaking Bad", "image": "tv.jpg"}

        self.importer._process_tv_list(
            [tv_entry(status="plantowatch", memo={"text": "Soon"})],
        )

        mock_tv_with_seasons.assert_called_once_with(1396, [])
        tv_obj = self.importer.bulk_media[MediaTypes.TV.value][0]
        self.assertEqual(tv_obj.status, Status.PLANNING.value)
        self.assertEqual(tv_obj.notes, "Soon")
        self.assertEqual(self.importer.bulk_media[MediaTypes.SEASON.value], [])
        self.assertEqual(self.importer.bulk_media[MediaTypes.EPISODE.value], [])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_tv_provider_outage_stops_import(self, mock_tv_with_seasons):
        """Test a provider failure other than "not found" aborts with the entry."""
        mock_tv_with_seasons.side_effect = provider_error(Sources.TMDB.value, 500)

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing entry",
        ):
            self.importer._process_tv_list([tv_entry()])

    def test_episode_image_falls_back_to_placeholder(self):
        """Test an episode TMDB does not list gets the placeholder image."""
        metadata = {
            "season/1": {"episodes": [{"episode_number": 1, "still_path": "/s.jpg"}]},
        }

        self.assertEqual(
            self.importer._get_episode_image({"number": 1}, 1, metadata),
            "https://image.tmdb.org/t/p/w500/s.jpg",
        )
        self.assertEqual(
            self.importer._get_episode_image({"number": 7}, 1, metadata),
            settings.IMG_NONE,
        )

    def test_history_date_prefers_watch_then_watchlist(self):
        """Test the history date falls back from watched to listed to now."""
        watched = "2023-01-02T00:00:00Z"
        listed = "2022-12-01T00:00:00Z"

        self.assertEqual(
            self.importer._get_history_date(
                {"last_watched_at": watched, "added_to_watchlist_at": listed},
            ),
            datetime(2023, 1, 2, tzinfo=UTC),
        )
        self.assertEqual(
            self.importer._get_history_date({"added_to_watchlist_at": listed}),
            datetime(2022, 12, 1, tzinfo=UTC),
        )
        before = datetime.now(tz=UTC)
        self.assertGreaterEqual(self.importer._get_history_date({}), before)
