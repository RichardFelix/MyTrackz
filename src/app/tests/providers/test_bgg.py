from unittest.mock import Mock, patch

import requests
from defusedxml import ElementTree
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from app.models import MediaTypes, Sources
from app.providers import bgg, services


def http_error(status_code):
    """Return the error the request helper raises for an HTTP status."""
    response = Mock(status_code=status_code, text="error")
    return requests.exceptions.HTTPError(response=response)


def xml(text):
    """Parse a BGG response body the way the request helper does."""
    return ElementTree.fromstring(text)


def search_response(count):
    """Return a BGG search response with the given number of games."""
    items = "".join(
        f'<item type="boardgame" id="{number}"><name value="Game {number}"/></item>'
        for number in range(1, count + 1)
    )
    return xml(f'<items total="{count}">{items}</items>')


THING_RESPONSE = """
<items>
  <item type="boardgame" id="30549">
    <thumbnail>https://example.com/thumb.jpg</thumbnail>
    <image>https://example.com/pandemic.jpg</image>
    <name type="primary" value="Pandemic"/>
    <name type="alternate" value="Pandemie"/>
    <description>Stop the outbreaks.</description>
    <yearpublished value="2008"/>
    <minplayers value="2"/>
    <maxplayers value="4"/>
    <playingtime value="45"/>
    <minage value="8"/>
    <link type="boardgamecategory" id="1" value="Medical"/>
    <link type="boardgamecategory" id="2" value="Cooperative"/>
    <link type="boardgamedesigner" id="3" value="Matt Leacock"/>
    <link type="boardgamepublisher" id="4" value="Z-Man Games"/>
    <link type="boardgamepublisher" id="5" value="Asmodee"/>
    <link type="boardgamepublisher" id="6" value="Filosofia"/>
    <link type="boardgamepublisher" id="7" value="Fourth Publisher"/>
    <link type="boardgameexpansion" id="40849" value="Pandemic: On the Brink"/>
    <link type="boardgameexpansion" id="99" value="Base Game" inbound="true"/>
    <statistics>
      <ratings>
        <usersrated value="120000"/>
        <average value="7.5432"/>
      </ratings>
    </statistics>
  </item>
</items>
"""


class BGGTests(TestCase):
    """Test the BoardGameGeek provider without calling the real API."""

    def setUp(self):
        """Start every test with an empty cache."""
        cache.clear()

    @patch("app.providers.bgg._fetch_thumbnails")
    @patch("app.providers.services.api_request")
    def test_search_paginates_one_cached_result_set(
        self,
        mock_api_request,
        mock_fetch_thumbnails,
    ):
        """Test BGG is searched once and the pages are cut from that result."""
        mock_api_request.return_value = search_response(25)
        mock_fetch_thumbnails.side_effect = lambda game_ids: {
            game_ids[0]: "https://example.com/first.jpg",
        }

        first_page = bgg.search("game", 1)
        second_page = bgg.search("game", 2)
        bgg.search("game", 1)

        mock_api_request.assert_called_once()
        self.assertEqual(mock_api_request.call_args.kwargs["response_format"], "xml")
        self.assertEqual(first_page["total_results"], 25)
        self.assertEqual(len(first_page["results"]), 20)
        self.assertEqual(
            [game["media_id"] for game in second_page["results"]],
            ["21", "22", "23", "24", "25"],
        )
        self.assertEqual(
            first_page["results"][0],
            {
                "media_id": "1",
                "source": Sources.BGG.value,
                "media_type": MediaTypes.BOARDGAME.value,
                "title": "Game 1",
                "image": "https://example.com/first.jpg",
            },
        )
        self.assertEqual(first_page["results"][1]["image"], settings.IMG_NONE)
        # Each page looks up its own thumbnails; a cached page looks up none.
        self.assertEqual(mock_fetch_thumbnails.call_count, 2)

    @patch("app.providers.services.api_request")
    def test_search_skips_entries_without_name(self, mock_api_request):
        """Test a search hit with no name is dropped."""
        mock_api_request.side_effect = [
            xml(
                '<items><item id="1"/>'
                '<item id="2"><name value="Named"/></item></items>',
            ),
            xml("<items/>"),
        ]

        data = bgg.search("game", 1)

        self.assertEqual([game["title"] for game in data["results"]], ["Named"])

    @patch("app.providers.services.api_request")
    def test_errors_become_provider_errors(self, mock_api_request):
        """Test HTTP errors are wrapped, with a hint when the token is rejected."""
        mock_api_request.side_effect = http_error(401)
        with self.assertRaises(services.ProviderAPIError) as context:
            bgg.search("game", 1)
        self.assertEqual(context.exception.status_code, 401)
        self.assertIn("BGG API requires authorization", str(context.exception))

        mock_api_request.side_effect = http_error(503)
        with self.assertRaises(services.ProviderAPIError) as context:
            bgg.boardgame("30549")
        self.assertEqual(context.exception.status_code, 503)

        with self.assertRaises(services.ProviderAPIError):
            bgg.hot()

    @patch("app.providers.services.api_request")
    def test_thumbnails_prefer_thumbnail_then_image(self, mock_api_request):
        """Test thumbnails fall back to the full image, then to nothing."""
        mock_api_request.return_value = xml(
            """
            <items>
              <item id="1"><thumbnail>t1.jpg</thumbnail><image>i1.jpg</image></item>
              <item id="2"><image>i2.jpg</image></item>
              <item id="3"/>
            </items>
            """,
        )

        thumbnails = bgg._fetch_thumbnails(["1", "2", "3"])

        self.assertEqual(thumbnails, {"1": "t1.jpg", "2": "i2.jpg"})
        self.assertEqual(mock_api_request.call_args.kwargs["params"], {"id": "1,2,3"})

    @patch("app.providers.services.api_request")
    def test_thumbnail_failure_does_not_break_search(self, mock_api_request):
        """Test search still answers when the thumbnail lookup fails."""
        mock_api_request.side_effect = [search_response(2), http_error(500)]

        data = bgg.search("game", 1)

        self.assertEqual(len(data["results"]), 2)
        for game in data["results"]:
            self.assertEqual(game["image"], settings.IMG_NONE)

    @patch("app.providers.services.api_request")
    def test_no_thumbnail_request_for_empty_page(self, mock_api_request):
        """Test nothing is requested when there are no games to look up."""
        self.assertEqual(bgg._fetch_thumbnails([]), {})
        mock_api_request.assert_not_called()

    @patch("app.providers.services.api_request")
    def test_hot_list(self, mock_api_request):
        """Test the hot list is normalized, capped and cached."""
        items = "".join(
            f'<item id="{number}" rank="{number}">'
            f'<thumbnail value="https://example.com/{number}.jpg"/>'
            f'<name value="Hot {number}"/></item>'
            for number in range(1, 26)
        )
        mock_api_request.return_value = xml(
            f'<items><item id="0"/>{items}'
            '<item id="99"><name value="No Thumbnail"/></item></items>',
        )

        data = bgg.hot()
        bgg.hot()

        mock_api_request.assert_called_once()
        # The first 20 entries are considered; the nameless one is dropped.
        self.assertEqual(len(data), 19)
        self.assertEqual(
            data[0],
            {
                "media_id": "1",
                "source": Sources.BGG.value,
                "media_type": MediaTypes.BOARDGAME.value,
                "title": "Hot 1",
                "image": "https://example.com/1.jpg",
            },
        )

    @patch("app.providers.services.api_request")
    def test_hot_entry_without_thumbnail(self, mock_api_request):
        """Test a hot entry with no thumbnail gets the placeholder image."""
        mock_api_request.return_value = xml(
            '<items><item id="99"><name value="No Thumbnail"/></item></items>',
        )

        self.assertEqual(bgg.hot()[0]["image"], settings.IMG_NONE)

    @patch("app.providers.services.api_request")
    def test_boardgame_metadata(self, mock_api_request):
        """Test a board game is normalized and cached."""
        mock_api_request.return_value = xml(THING_RESPONSE)

        data = bgg.boardgame("30549")
        bgg.boardgame("30549")

        mock_api_request.assert_called_once()
        self.assertEqual(
            mock_api_request.call_args.kwargs["params"],
            {"id": "30549", "stats": "1"},
        )
        self.assertEqual(data["title"], "Pandemic")
        self.assertEqual(data["image"], "https://example.com/pandemic.jpg")
        self.assertEqual(data["synopsis"], "Stop the outbreaks.")
        self.assertEqual(data["genres"], ["Medical", "Cooperative"])
        self.assertEqual(data["score"], 7.5)
        self.assertEqual(data["score_count"], 120000)
        self.assertEqual(
            data["source_url"],
            "https://boardgamegeek.com/boardgame/30549",
        )
        self.assertEqual(
            data["details"],
            {
                "year": "2008",
                "players": "2-4 players",
                "playtime": "45 min",
                "min_age": "8+",
                "designers": "Matt Leacock",
                "publishers": "Z-Man Games, Asmodee, Filosofia",
            },
        )
        # Only the game's own expansions are listed, not the game it expands.
        self.assertEqual(
            [game["media_id"] for game in data["related"]["expansions"]],
            ["40849"],
        )

    @patch("app.providers.services.api_request")
    def test_unknown_boardgame_is_not_found(self, mock_api_request):
        """Test an ID BGG has no game for is a 404, not a crash."""
        mock_api_request.return_value = xml("<items/>")

        with self.assertRaises(services.ProviderAPIError) as context:
            bgg.boardgame("0")

        self.assertEqual(context.exception.status_code, 404)

    def test_sparse_boardgame_fields(self):
        """Test a game with almost no data yields empty values, not errors."""
        item = xml('<item id="1"/>')

        self.assertEqual(bgg.get_title(item), "Unknown")
        self.assertEqual(bgg.get_image(item), settings.IMG_NONE)
        self.assertEqual(bgg.get_description(item), "No synopsis available")
        self.assertIsNone(bgg.get_year(item))
        self.assertIsNone(bgg.get_players(item))
        self.assertIsNone(bgg.get_playtime(item))
        self.assertIsNone(bgg.get_min_age(item))
        self.assertIsNone(bgg.get_score(item))
        self.assertIsNone(bgg.get_score_count(item))
        self.assertIsNone(bgg.get_categories(item))
        self.assertIsNone(bgg.get_designers(item))
        self.assertIsNone(bgg.get_publishers(item))
        self.assertEqual(bgg.get_expansions_related(item), {})

    def test_player_count_and_bad_ratings(self):
        """Test a fixed player count reads naturally and bad ratings are ignored."""
        item = xml(
            """
            <item id="1">
              <minplayers value="2"/>
              <maxplayers value="2"/>
              <statistics>
                <ratings>
                  <usersrated value="many"/>
                  <average value="n/a"/>
                </ratings>
              </statistics>
            </item>
            """,
        )

        self.assertEqual(bgg.get_players(item), "2 players")
        self.assertIsNone(bgg.get_score(item))
        self.assertIsNone(bgg.get_score_count(item))
