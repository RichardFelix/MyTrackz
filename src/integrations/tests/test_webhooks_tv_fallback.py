from unittest.mock import call, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Episode, Item, MediaTypes, Season
from app.providers import services
from integrations.webhooks.emby import EmbyWebhookProcessor
from integrations.webhooks.jellyfin import JellyfinWebhookProcessor
from integrations.webhooks.plex import PlexWebhookProcessor


def tmdb_search_results(*results):
    """Return a provider-style TMDB search response."""
    return {"results": list(results)}


def tmdb_tv_metadata(
    media_id=1685,
    *,
    series_title="Project Runway",
    season_number=22,
    episode_number=3,
    episode_id=7393222,
    episode_title="In It to Win It",
    tvdb_id=None,
):
    """Return the minimal metadata required by TV webhook processing."""
    return {
        "media_id": media_id,
        "title": series_title,
        "image": "https://example.com/show.jpg",
        "tvdb_id": tvdb_id,
        f"season/{season_number}": {
            "image": "https://example.com/season.jpg",
            "episodes": [
                {
                    "id": episode_id,
                    "episode_number": episode_number,
                    "name": episode_title,
                    "still_path": None,
                },
            ],
        },
    }


def plex_payload(*, tmdb_id="7393222", series_title="Project Runway"):
    """Return a Plex scrobble payload for a TV episode."""
    guids = [{"id": f"tmdb://{tmdb_id}"}] if tmdb_id else []
    return {
        "event": "media.scrobble",
        "Account": {"title": "testuser"},
        "Metadata": {
            "type": "episode",
            "title": "In It to Win It",
            "grandparentTitle": series_title,
            "parentIndex": 22,
            "index": 3,
            "Guid": guids,
        },
    }


def jellyfin_payload(*, tmdb_id="7393222"):
    """Return a Jellyfin stop payload for a TV episode."""
    provider_ids = {"Tmdb": tmdb_id} if tmdb_id else {}
    return {
        "Event": "Stop",
        "Item": {
            "Type": "Episode",
            "Name": "In It to Win It",
            "SeriesName": "Project Runway",
            "ParentIndexNumber": 22,
            "IndexNumber": 3,
            "ProviderIds": provider_ids,
            "UserData": {"Played": True},
        },
    }


def emby_payload(*, tmdb_id="7393222"):
    """Return an Emby stop payload for a TV episode."""
    provider_ids = {"Tmdb": tmdb_id} if tmdb_id else {}
    return {
        "Event": "playback.stop",
        "Item": {
            "Type": "Episode",
            "Name": "In It to Win It",
            "SeriesName": "Project Runway",
            "ParentIndexNumber": 22,
            "IndexNumber": 3,
            "ProviderIds": provider_ids,
        },
        "PlaybackInfo": {"PlayedToCompletion": True},
    }


class TVWebhookTitleFallbackTests(TestCase):
    """Tests for conservative TV title and episode fallback matching."""

    def setUp(self):
        """Create a webhook user shared by all processor tests."""
        credentials = {
            "username": "testuser",
            "token": "test-token",
            "plex_usernames": "testuser",
            "anime_enabled": False,
        }
        self.user = get_user_model().objects.create_superuser(**credentials)
        self.user.auto_mark_previous_episodes = False
        self.user.save(update_fields=["auto_mark_previous_episodes"])

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_plex_title_with_year_retries_without_year(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """A Plex disambiguation year does not prevent an episode ID match."""
        mock_search.side_effect = (
            tmdb_search_results(),
            tmdb_search_results({"media_id": 90266, "title": "Press Your Luck"}),
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata(
            media_id=90266,
            series_title="Press Your Luck",
            season_number=7,
            episode_number=3,
            episode_id=6727877,
            episode_title="Big Bass, No Whammy",
        )
        payload = plex_payload(
            tmdb_id="6727877",
            series_title="Press Your Luck (2019)",
        )
        payload["Metadata"].update(
            {
                "parentIndex": 7,
                "index": 3,
                "title": "Big Bass, No Whammy",
            },
        )

        PlexWebhookProcessor().process_payload(payload, self.user)

        self.assertEqual(
            mock_search.call_args_list,
            [
                call(MediaTypes.TV.value, "Press Your Luck (2019)", 1),
                call(MediaTypes.TV.value, "Press Your Luck", 1),
            ],
        )
        mock_handle_tv_episode.assert_called_once_with(
            90266,
            7,
            3,
            payload,
            self.user,
        )

    @patch("app.providers.tmdb.tv")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_plex_tmdb_episode_id_creates_project_runway_episode(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_tv,
    ):
        """A TMDB-only Plex payload creates and scrobbles the resolved episode."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "Project Runway"},
            {"media_id": 42763, "title": "Project Runway All Stars"},
        )
        metadata_by_id = {
            1685: tmdb_tv_metadata(),
            42763: tmdb_tv_metadata(
                media_id=42763,
                series_title="Project Runway All Stars",
                episode_id=999,
            ),
        }
        mock_tv_with_seasons.side_effect = (
            lambda media_id, _seasons: metadata_by_id[media_id]
        )
        mock_tv.return_value = {"related": {"seasons": []}}

        PlexWebhookProcessor().process_payload(plex_payload(), self.user)

        tv_item = Item.objects.get(
            media_type=MediaTypes.TV.value,
            media_id="1685",
        )
        self.assertEqual(tv_item.title, "Project Runway")
        self.assertTrue(TV.objects.filter(item=tv_item, user=self.user).exists())
        self.assertTrue(
            Season.objects.filter(
                related_tv__item=tv_item,
                item__season_number=22,
            ).exists(),
        )
        self.assertTrue(
            Episode.objects.filter(
                related_season__related_tv__item=tv_item,
                item__season_number=22,
                item__episode_number=3,
            ).exists(),
        )

    @patch.object(Season, "mark_previous_episodes_watched")
    @patch("app.providers.tmdb.tv")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_scrobble_applies_previous_episode_preference(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_tv,
        mock_mark_previous,
    ):
        """A successful webhook scrobble backfills earlier episodes when enabled."""
        self.user.auto_mark_previous_episodes = True
        self.user.save(update_fields=["auto_mark_previous_episodes"])
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "Project Runway"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata()
        mock_tv.return_value = {"related": {"seasons": []}}
        payload = plex_payload()

        PlexWebhookProcessor().process_payload(payload, self.user)

        episode = Episode.objects.get(
            related_season__user=self.user,
            item__season_number=22,
            item__episode_number=3,
        )
        mock_mark_previous.assert_called_once_with(3, episode.end_date)

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_tmdb_episode_id_fallback_is_shared_by_all_media_servers(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """Plex, Jellyfin, and Emby all use the shared TMDB episode resolver."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "Project Runway"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata()

        cases = (
            (PlexWebhookProcessor(), plex_payload()),
            (JellyfinWebhookProcessor(), jellyfin_payload()),
            (EmbyWebhookProcessor(), emby_payload()),
        )
        for processor, payload in cases:
            with self.subTest(processor=type(processor).__name__):
                processor.process_payload(payload, self.user)
                mock_handle_tv_episode.assert_called_once_with(
                    1685,
                    22,
                    3,
                    payload,
                    self.user,
                )
                mock_handle_tv_episode.reset_mock()

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    @patch("app.providers.tmdb.find")
    @patch("integrations.webhooks.tv.tvdb_provider.episode")
    def test_provider_id_misses_continue_to_title_fallback(
        self,
        mock_tvdb_episode,
        mock_tmdb_find,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """TVDB failures and IMDB misses do not prevent title resolution."""
        mock_tvdb_episode.side_effect = requests.exceptions.ConnectionError("offline")
        mock_tmdb_find.return_value = {"tv_episode_results": []}
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "Project Runway"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata()
        payload = plex_payload()
        payload["Metadata"]["Guid"].extend(
            (
                {"id": "tvdb://12345"},
                {"id": "imdb://tt12345"},
            ),
        )

        PlexWebhookProcessor().process_payload(payload, self.user)

        mock_tmdb_find.assert_called_once_with("tt12345", "imdb_id")
        mock_handle_tv_episode.assert_called_once_with(
            1685,
            22,
            3,
            payload,
            self.user,
        )

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_no_id_payload_uses_unique_normalized_exact_title(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """A no-ID episode resolves only through a unique exact series title."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "project   runway"},
            {"media_id": 42763, "title": "Project Runway All Stars"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata()
        payload = plex_payload(tmdb_id="", series_title="  Project Runway ")

        PlexWebhookProcessor().process_payload(payload, self.user)

        mock_handle_tv_episode.assert_called_once_with(
            1685,
            22,
            3,
            payload,
            self.user,
        )
        mock_tv_with_seasons.assert_called_once_with(1685, [22])

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_episode_title_disambiguates_duplicate_series_titles(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """Episode title selects one candidate with the same exact series title."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1, "title": "Project Runway"},
            {"media_id": 2, "title": "Project Runway"},
        )
        mock_tv_with_seasons.side_effect = (
            tmdb_tv_metadata(
                media_id=1,
                episode_id=10,
                episode_title="Different Episode",
            ),
            tmdb_tv_metadata(media_id=2, episode_id=20),
        )
        payload = plex_payload(tmdb_id="")

        PlexWebhookProcessor().process_payload(payload, self.user)

        mock_handle_tv_episode.assert_called_once_with(
            2,
            22,
            3,
            payload,
            self.user,
        )

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_ambiguous_title_match_is_skipped(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """Two indistinguishable title matches do not mutate tracking state."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1, "title": "Project Runway"},
            {"media_id": 2, "title": "Project Runway"},
        )
        mock_tv_with_seasons.side_effect = (
            tmdb_tv_metadata(media_id=1),
            tmdb_tv_metadata(media_id=2),
        )

        PlexWebhookProcessor().process_payload(
            plex_payload(tmdb_id=""),
            self.user,
        )

        mock_handle_tv_episode.assert_not_called()

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_conflicting_tmdb_episode_id_does_not_downgrade_to_title_only(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """A supplied but conflicting TMDB ID causes the event to be skipped."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "Project Runway"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata(episode_id=999)

        PlexWebhookProcessor().process_payload(plex_payload(), self.user)

        mock_handle_tv_episode.assert_not_called()

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_missing_episode_is_skipped(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_handle_tv_episode,
    ):
        """A matching series without the requested episode is not accepted."""
        mock_search.return_value = tmdb_search_results(
            {"media_id": 1685, "title": "Project Runway"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata(episode_number=2)

        PlexWebhookProcessor().process_payload(plex_payload(), self.user)

        mock_handle_tv_episode.assert_not_called()

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_tv_episode")
    @patch("app.providers.tmdb.search")
    def test_provider_failure_is_skipped(self, mock_search, mock_handle_tv_episode):
        """A TMDB provider error leaves tracking state unchanged."""
        response = requests.Response()
        response.status_code = 503
        error = requests.exceptions.HTTPError(response=response)
        mock_search.side_effect = services.ProviderAPIError(
            "TMDB",
            error,
            "temporary failure",
        )

        PlexWebhookProcessor().process_payload(plex_payload(), self.user)

        mock_handle_tv_episode.assert_not_called()

    @patch("app.providers.tmdb.search")
    def test_invalid_coordinates_do_not_search(self, mock_search):
        """Malformed episode coordinates are rejected before provider calls."""
        for invalid_season in (-1, None):
            with self.subTest(season=invalid_season):
                payload = plex_payload()
                payload["Metadata"]["parentIndex"] = invalid_season
                PlexWebhookProcessor().process_payload(payload, self.user)

        mock_search.assert_not_called()

    @patch("integrations.webhooks.base.BaseWebhookProcessor._handle_anime")
    @patch("integrations.webhooks.tv.anime_mappings.get_mal_id_from_tvdb")
    @patch("integrations.webhooks.tv.anime_mappings.fetch_mapping_data")
    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.search")
    def test_title_resolved_episode_preserves_anime_mapping(
        self,
        mock_search,
        mock_tv_with_seasons,
        mock_fetch_mapping,
        mock_get_mal_id,
        mock_handle_anime,
    ):
        """Resolved TMDB shows still route through TVDB-to-MAL anime mapping."""
        self.user.anime_enabled = True
        self.user.save(update_fields=["anime_enabled"])
        mock_search.return_value = tmdb_search_results(
            {"media_id": 209867, "title": "Frieren: Beyond Journey's End"},
        )
        mock_tv_with_seasons.return_value = tmdb_tv_metadata(
            media_id=209867,
            series_title="Frieren: Beyond Journey's End",
            season_number=1,
            episode_number=1,
            episode_id=3946240,
            episode_title="The Journey's End",
            tvdb_id=424536,
        )
        mock_fetch_mapping.return_value = {"mapping": "data"}
        mock_get_mal_id.return_value = (52991, 1)
        payload = plex_payload(
            tmdb_id="3946240",
            series_title="Frieren: Beyond Journey's End",
        )
        payload["Metadata"]["parentIndex"] = 1
        payload["Metadata"]["index"] = 1
        payload["Metadata"]["title"] = "The Journey's End"

        PlexWebhookProcessor().process_payload(payload, self.user)

        mock_get_mal_id.assert_called_once_with(
            {"mapping": "data"},
            424536,
            1,
            1,
        )
        mock_handle_anime.assert_called_once_with(52991, 1, payload, self.user)
