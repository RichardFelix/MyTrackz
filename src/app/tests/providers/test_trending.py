from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from app.models import MediaTypes
from app.providers import bgg, igdb, mal, tmdb

REQUIRED_KEYS = {"media_id", "source", "media_type", "title", "image"}


class Trending(TestCase):
    """Test the external API calls for trending media."""

    def _assert_trending_shape(self, results, media_type):
        self.assertTrue(results, f"expected some trending {media_type} results")
        for entry in results:
            self.assertTrue(all(key in entry for key in REQUIRED_KEYS))
            self.assertEqual(entry["media_type"], media_type)

    def test_tmdb_trending_excludes_anime(self):
        """Anime entries in TMDB trending are dropped; Western animation stays.

        Anime has its own MAL-backed trending section, so TMDB's TV/movie
        sections keep only live-action and Western animation.
        """
        cache.clear()
        self.addCleanup(cache.clear)

        results_payload = {
            "results": [
                {
                    "id": 1,
                    "name": "Live Action Show",
                    "poster_path": None,
                    "genre_ids": [18],
                    "original_language": "en",
                },
                {
                    "id": 2,
                    "name": "Western Cartoon",
                    "poster_path": None,
                    "genre_ids": [tmdb.ANIMATION_GENRE_ID],
                    "original_language": "en",
                },
                {
                    "id": 3,
                    "name": "Anime Series",
                    "poster_path": None,
                    "genre_ids": [tmdb.ANIMATION_GENRE_ID, 10759],
                    "original_language": "ja",
                },
                {
                    "id": 4,
                    "name": "Donghua Series",
                    "poster_path": None,
                    "genre_ids": [tmdb.ANIMATION_GENRE_ID],
                    "original_language": "zh",
                },
                {
                    "id": 5,
                    "name": "Live Action Japanese Drama",
                    "poster_path": None,
                    "genre_ids": [18],
                    "original_language": "ja",
                },
            ],
        }
        with patch(
            "app.providers.services.api_request",
            return_value=results_payload,
        ):
            results = tmdb.trending(MediaTypes.TV.value)

        titles = [entry["title"] for entry in results]
        self.assertEqual(
            titles,
            ["Live Action Show", "Western Cartoon", "Live Action Japanese Drama"],
        )

    def test_tmdb_trending_movies(self):
        """TMDB weekly trending movies return well-formed entries."""
        results = tmdb.trending(MediaTypes.MOVIE.value)
        self._assert_trending_shape(results, MediaTypes.MOVIE.value)

    def test_tmdb_trending_tv(self):
        """TMDB weekly trending TV shows return well-formed entries."""
        results = tmdb.trending(MediaTypes.TV.value)
        self._assert_trending_shape(results, MediaTypes.TV.value)

    def test_mal_trending_anime(self):
        """MAL top airing anime return well-formed entries."""
        results = mal.trending(MediaTypes.ANIME.value)
        self._assert_trending_shape(results, MediaTypes.ANIME.value)

    def test_mal_trending_manga(self):
        """MAL most popular manga return well-formed entries."""
        results = mal.trending(MediaTypes.MANGA.value)
        self._assert_trending_shape(results, MediaTypes.MANGA.value)

    def test_igdb_trending_games(self):
        """IGDB popular recent games return well-formed entries."""
        results = igdb.trending()
        self._assert_trending_shape(results, MediaTypes.GAME.value)

    def test_bgg_hot_boardgames(self):
        """BGG hot board games return well-formed entries."""
        results = bgg.hot()
        self._assert_trending_shape(results, MediaTypes.BOARDGAME.value)
