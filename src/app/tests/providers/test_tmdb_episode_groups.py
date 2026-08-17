from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from app.providers import tmdb

FUTURAMA_TMDB_ID = 615
ORIGINAL_FOX_SEASONS = 5


def _episode(episode_id, season_number, episode_number, name):
    return {
        "id": episode_id,
        "order": episode_number - 1,
        "season_number": season_number,
        "episode_number": episode_number,
        "name": name,
        "overview": f"Overview for {name}",
        "air_date": "2026-08-03",
        "runtime": 25,
        "still_path": f"/{episode_id}.jpg",
        "vote_average": 7.5,
        "vote_count": 2,
    }


def _futurama_episode_group():
    groups = []
    for season_number in range(1, 15):
        source_season = (
            season_number
            if season_number <= ORIGINAL_FOX_SEASONS
            else season_number - 3
        )
        groups.append(
            {
                "order": season_number,
                "name": f"Season {season_number}",
                "episodes": [
                    _episode(
                        7000 + season_number,
                        source_season,
                        1,
                        f"Season {season_number} Premiere",
                    ),
                ],
            },
        )
    groups[-1]["episodes"] = [
        _episode(7269626, 11, 1, "Beef"),
        _episode(7269629, 11, 2, "Catfish Hunter"),
    ]
    return {"id": tmdb.TV_EPISODE_GROUP_OVERRIDES["615"], "groups": groups}


def _tv_response(media_id=615):
    return {
        "id": media_id,
        "name": "Futurama" if media_id == FUTURAMA_TMDB_ID else "Ordinary Show",
        "number_of_episodes": 2,
        "number_of_seasons": 11,
        "poster_path": "/show.jpg",
        "overview": "A show overview.",
        "genres": [],
        "vote_average": 8.0,
        "vote_count": 100,
        "first_air_date": "1999-03-28",
        "last_air_date": "2026-08-03",
        "status": "Returning Series",
        "episode_run_time": [25],
        "production_companies": [],
        "production_countries": [],
        "spoken_languages": [],
        "seasons": [
            {
                "poster_path": "/season.jpg",
                "season_number": 11,
                "name": "Season 11",
                "air_date": "2026-08-03",
                "episode_count": 2,
            },
        ],
        "recommendations": {"results": []},
        "external_ids": {},
        "watch/providers": {"results": {}},
        "last_episode_to_air": {"id": 7269626, "season_number": 11},
        "next_episode_to_air": {"id": 7269629, "season_number": 11},
    }


class TMDBEpisodeGroupTests(SimpleTestCase):
    """Verify the Futurama override is isolated from normal TMDB shows."""

    def setUp(self):
        """Clear provider caches between tests."""
        cache.clear()

    @patch("app.providers.tmdb.services.api_request")
    def test_futurama_tv_uses_digital_order(self, mock_api_request):
        """Futurama's TV details advertise the 14 digital seasons."""
        cache.set("tmdb_tv_615", {"details": {"seasons": 11}})
        mock_api_request.side_effect = [
            _tv_response(),
            _futurama_episode_group(),
        ]

        metadata = tmdb.tv("615")

        self.assertEqual(metadata["details"]["seasons"], 14)
        self.assertEqual(metadata["details"]["episodes"], 15)
        self.assertEqual(metadata["last_episode_season"], 14)
        self.assertEqual(metadata["next_episode_season"], 14)
        self.assertEqual(metadata["related"]["seasons"][-1]["season_number"], 14)
        self.assertEqual(mock_api_request.call_count, 2)

    def test_episode_group_cache_keys_do_not_collide_with_default_order(self):
        """Futurama's alternate metadata cannot reuse old default-order caches."""
        self.assertEqual(tmdb.metadata_cache_key("tv", "999"), "tmdb_tv_999")
        self.assertEqual(
            tmdb.metadata_cache_key("season", "615", 14),
            "tmdb_season_615_14_episode_group_5ad25f9b0e0a266c3101674b",
        )

    @patch("app.providers.tmdb.services.api_request")
    def test_futurama_season_uses_group_episode_numbers(self, mock_api_request):
        """Digital season episode positions become one-based progress numbers."""
        response = _tv_response()
        response["season/11"] = {
            "name": "Season 11",
            "season_number": 11,
            "overview": "Production season overview.",
            "poster_path": "/season-11.jpg",
            "vote_average": 7.5,
        }
        response["season/11/watch/providers"] = {"results": {}}
        mock_api_request.side_effect = [_futurama_episode_group(), response]

        metadata = tmdb.tv_with_seasons("615", [14])
        season = metadata["season/14"]

        self.assertEqual(season["season_number"], 14)
        self.assertEqual(season["max_progress"], 2)
        self.assertEqual(season["episodes"][0]["episode_number"], 1)
        self.assertEqual(season["episodes"][1]["episode_number"], 2)
        self.assertEqual(season["episodes"][1]["name"], "Catfish Hunter")

    @patch("app.providers.tmdb.services.api_request")
    def test_other_tv_shows_keep_default_order(self, mock_api_request):
        """An ordinary TMDB show does not fetch or apply an episode group."""
        mock_api_request.return_value = _tv_response(media_id=999)

        metadata = tmdb.tv("999")

        self.assertEqual(metadata["details"]["seasons"], 11)
        self.assertEqual(metadata["related"]["seasons"][0]["season_number"], 11)
        mock_api_request.assert_called_once()

    @patch("app.providers.tmdb.services.api_request")
    def test_external_episode_id_translates_to_digital_order(self, mock_api_request):
        """Webhook episode IDs resolve to Futurama's digital coordinates."""
        mock_api_request.return_value = _futurama_episode_group()

        coordinates = tmdb.get_episode_group_coordinates("615", 7269629)

        self.assertEqual(coordinates, (14, 2))

    @patch("app.providers.tmdb.services.api_request")
    def test_external_episode_id_for_other_show_is_unchanged(self, mock_api_request):
        """Episode coordinate translation is disabled for all other shows."""
        coordinates = tmdb.get_episode_group_coordinates("999", 1)

        self.assertIsNone(coordinates)
        mock_api_request.assert_not_called()
