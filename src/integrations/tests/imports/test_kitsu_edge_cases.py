from datetime import UTC, datetime
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import Anime, Item, Manga, MediaTypes, Sources, Status
from integrations.imports import kitsu
from integrations.imports.helpers import MediaImportError, MediaImportUnexpectedError


def mapping(mapping_id, site, external_id):
    """Return a Kitsu mapping record."""
    return {
        "id": mapping_id,
        "type": "mappings",
        "attributes": {"externalSite": site, "externalId": external_id},
    }


def media(media_id, media_type, title, mapping_ids, **attributes):
    """Return a Kitsu anime or manga record."""
    return {
        "id": media_id,
        "type": media_type,
        "attributes": {
            "canonicalTitle": title,
            "posterImage": {"medium": "medium.jpg", "original": "original.jpg"},
            **attributes,
        },
        "relationships": {
            "mappings": {"data": [{"id": mapping_id} for mapping_id in mapping_ids]},
        },
    }


def entry(media_type, media_id, **attributes):
    """Return a Kitsu library entry pointing at a media record."""
    return {
        "attributes": {
            "status": "current",
            "progress": 5,
            "ratingTwenty": 16,
            "reconsuming": False,
            "reconsumeCount": 0,
            "startedAt": "2023-01-01T10:20:30.400Z",
            "finishedAt": None,
            "notes": None,
            "updatedAt": "2023-02-01T00:00:00.000Z",
            **attributes,
        },
        "relationships": {
            media_type: {
                "data": {"id": media_id} if media_id else None,
                "links": {"related": f"https://kitsu.app/api/edge/{media_type}/x"},
            },
        },
    }


class KitsuEdgeCaseTests(TestCase):
    """Test the Kitsu importer beyond the happy path."""

    def setUp(self):
        """Create user and importer for the tests."""
        credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)
        self.importer = kitsu.KitsuImporter("someone", self.user, "new")

    def lookups(self, media_records, mappings):
        """Return the lookup tables the importer builds from a response."""
        return (
            {record["id"]: record for record in media_records},
            {record["id"]: record for record in mappings},
        )

    @patch("app.providers.services.api_request")
    def test_unknown_username_is_reported(self, mock_api_request):
        """Test a username Kitsu does not know is reported in plain words."""
        mock_api_request.return_value = {"data": []}

        with self.assertRaisesMessage(MediaImportError, "User someone not found."):
            self.importer._get_kitsu_id("someone")

    @patch("app.providers.services.api_request")
    def test_ambiguous_username_asks_for_user_id(self, mock_api_request):
        """Test a username shared by several accounts asks for the user ID."""
        mock_api_request.return_value = {"data": [{"id": "1"}, {"id": "2"}]}

        with self.assertRaisesMessage(MediaImportError, "please use your user ID"):
            self.importer._get_kitsu_id("someone")

    @patch("app.providers.services.api_request")
    def test_username_is_resolved_before_import(self, mock_api_request):
        """Test a username is looked up and the library fetched by its ID."""
        empty_library = {"data": [], "links": {}}
        mock_api_request.side_effect = [
            {"data": [{"id": "99"}]},
            empty_library,
            empty_library,
        ]

        imported_counts, warnings = self.importer.import_data()

        self.assertEqual(imported_counts, {})
        self.assertEqual(warnings, "")
        lookup_call, anime_call, manga_call = mock_api_request.call_args_list
        self.assertEqual(lookup_call.kwargs["params"], {"filter[name]": "someone"})
        self.assertEqual(anime_call.kwargs["params"]["filter[user_id]"], "99")
        self.assertEqual(anime_call.kwargs["params"]["filter[kind]"], "anime")
        self.assertEqual(manga_call.kwargs["params"]["filter[kind]"], "manga")

    @patch("app.providers.services.api_request")
    def test_library_pages_are_followed(self, mock_api_request):
        """Test every page of the library is collected."""
        next_url = "https://kitsu.app/api/edge/library-entries?page[offset]=500"
        mock_api_request.side_effect = [
            {"data": [1], "included": ["a"], "links": {"next": next_url}},
            {"data": [2], "links": {}},
        ]
        self.importer.kitsu_id = "99"

        data = self.importer._get_media_response(MediaTypes.ANIME.value)

        self.assertEqual(data, {"entries": [1, 2], "included": ["a"]})
        second_call = mock_api_request.call_args_list[1]
        self.assertEqual(second_call.args[2], next_url)
        # The "next" link already carries the filters.
        self.assertEqual(second_call.kwargs["params"], {})

    def test_completed_rewatches_are_imported_as_separate_entries(self):
        """Test each finished rewatch is its own completed entry."""
        media_lookup, mapping_lookup = self.lookups(
            [media("10", "anime", "Bebop", ["m1"], episodeCount=26)],
            [mapping("m1", "myanimelist/anime", "1")],
        )

        self.importer._process_entry(
            entry("anime", "10", status="completed", reconsumeCount=2, progress=26),
            MediaTypes.ANIME.value,
            media_lookup,
            mapping_lookup,
        )

        instances = self.importer.bulk_media[MediaTypes.ANIME.value]
        self.assertEqual(len(instances), 3)
        for instance in instances:
            self.assertIsInstance(instance, Anime)
            self.assertEqual(instance.status, Status.COMPLETED.value)
            self.assertEqual(instance.progress, 26)
            self.assertEqual(instance.score, 8)
            self.assertEqual(instance.notes, "")

    def test_active_rewatch_is_in_progress(self):
        """Test a rewatch that is under way is tracked as in progress."""
        media_lookup, mapping_lookup = self.lookups(
            [media("10", "anime", "Bebop", ["m1"], episodeCount=26)],
            [mapping("m1", "myanimelist/anime", "1")],
        )

        self.importer._process_entry(
            entry("anime", "10", status="completed", reconsuming=True, progress=4),
            MediaTypes.ANIME.value,
            media_lookup,
            mapping_lookup,
        )

        instances = self.importer.bulk_media[MediaTypes.ANIME.value]
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].status, Status.IN_PROGRESS.value)
        self.assertEqual(instances[0].progress, 4)

    def test_manga_uses_mangaupdates_when_mal_is_missing(self):
        """Test a manga without a MyAnimeList ID is matched on MangaUpdates."""
        old_id, new_id = next(iter(self.importer.kitsu_mu_mapping.items()))
        media_lookup, mapping_lookup = self.lookups(
            [
                media("20", "manga", "New ID", ["m1"], chapterCount=10),
                media("21", "manga", "Old ID", ["m2"], chapterCount=10),
            ],
            [
                mapping("m1", "mangaupdates", "abc123"),
                mapping("m2", "mangaupdates", old_id),
            ],
        )

        for media_id in ("20", "21"):
            self.importer._process_entry(
                entry("manga", media_id),
                MediaTypes.MANGA.value,
                media_lookup,
                mapping_lookup,
            )

        new_style, old_style = self.importer.bulk_media[MediaTypes.MANGA.value]
        self.assertIsInstance(new_style, Manga)
        self.assertEqual(new_style.item.source, Sources.MANGAUPDATES.value)
        self.assertEqual(new_style.item.media_id, str(int("abc123", 36)))
        self.assertEqual(old_style.item.media_id, str(int(new_id, 36)))

    @patch("app.providers.services.api_request")
    def test_unmatched_entries_become_warnings(self, mock_api_request):
        """Test entries with no usable ID are reported and the rest imported."""
        mock_api_request.return_value = {
            "data": [
                entry("anime", "10"),
                entry("anime", "11"),
                entry("anime", "12"),
                entry("anime", "13"),
            ],
            "included": [
                media("10", "anime", "Good", ["m1"], episodeCount=12),
                media("11", "anime", "No Mappings", [], episodeCount=12),
                media("12", "anime", "Bad MAL ID", ["m2"], episodeCount=12),
                media("13", "anime", "Unknown MU ID", ["m3"], episodeCount=12),
                mapping("m1", "myanimelist/anime", "1"),
                mapping("m2", "myanimelist/anime", "anime"),
                mapping("m3", "mangaupdates", "999999999999"),
            ],
            "links": {},
        }
        self.importer.kitsu_id = "99"

        self.importer._process_media_type(MediaTypes.ANIME.value)

        instances = self.importer.bulk_media[MediaTypes.ANIME.value]
        self.assertEqual([instance.item.title for instance in instances], ["Good"])
        self.assertEqual(
            self.importer.warnings,
            [
                "No Mappings: No valid external ID found.",
                "Bad MAL ID: No valid external ID found.",
                "Unknown MU ID: No valid external ID found.",
            ],
        )

    @patch("app.providers.services.api_request")
    def test_hidden_entry_is_fetched_from_related_url(self, mock_api_request):
        """Test an entry Kitsu hides from the list is fetched individually."""
        mock_api_request.return_value = {
            "data": media("30", "anime", "Hidden", ["m1"], episodeCount=12),
            "included": [mapping("m1", "myanimelist/anime", "77")],
        }

        self.importer._process_entry(
            entry("anime", None),
            MediaTypes.ANIME.value,
            {},
            {},
        )

        self.assertEqual(
            mock_api_request.call_args.args[2],
            "https://kitsu.app/api/edge/anime/x",
        )
        instance = self.importer.bulk_media[MediaTypes.ANIME.value][0]
        self.assertEqual(instance.item.media_id, "77")
        self.assertEqual(instance.item.title, "Hidden")

    def test_hidden_entry_without_related_url_is_reported(self):
        """Test a hidden entry that cannot be fetched is reported."""
        hidden = entry("anime", None)
        hidden["relationships"]["anime"]["links"]["related"] = None

        with self.assertRaisesMessage(MediaImportError, "missing media data"):
            self.importer._process_entry(hidden, MediaTypes.ANIME.value, {}, {})

    @patch("app.providers.services.api_request")
    def test_unexpected_error_names_the_entry(self, mock_api_request):
        """Test a malformed entry is reported with its title."""
        broken = entry("anime", "10")
        del broken["attributes"]["updatedAt"]
        mock_api_request.return_value = {
            "data": [broken],
            "included": [
                media("10", "anime", "Bebop", ["m1"], episodeCount=26),
                mapping("m1", "myanimelist/anime", "1"),
            ],
            "links": {},
        }
        self.importer.kitsu_id = "99"

        with self.assertRaisesMessage(
            MediaImportUnexpectedError,
            "Error processing entry: Bebop (10)",
        ):
            self.importer._process_media_type(MediaTypes.ANIME.value)

    def test_new_mode_skips_existing_anime(self):
        """Test "new" mode leaves an anime the user already tracks alone."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Bebop",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(item=item, user=self.user, status=Status.PLANNING.value)
        importer = kitsu.KitsuImporter("someone", self.user, "new")
        media_lookup, mapping_lookup = self.lookups(
            [media("10", "anime", "Bebop", ["m1"], episodeCount=26)],
            [mapping("m1", "myanimelist/anime", "1")],
        )

        importer._process_entry(
            entry("anime", "10"),
            MediaTypes.ANIME.value,
            media_lookup,
            mapping_lookup,
        )

        self.assertEqual(importer.bulk_media[MediaTypes.ANIME.value], [])

    def test_image_falls_back_to_original_then_placeholder(self):
        """Test the poster falls back from medium to original to the placeholder."""
        self.assertEqual(
            self.importer._get_image_url(
                {"attributes": {"posterImage": {"medium": "m.jpg"}}},
            ),
            "m.jpg",
        )
        self.assertEqual(
            self.importer._get_image_url(
                {"attributes": {"posterImage": {"original": "o.jpg"}}},
            ),
            "o.jpg",
        )
        self.assertEqual(
            self.importer._get_image_url({"attributes": {"posterImage": {}}}),
            settings.IMG_NONE,
        )

    def test_dates_drop_seconds(self):
        """Test imported dates are stored to the minute."""
        self.assertEqual(
            self.importer._get_date("2023-01-01T10:20:30.400Z"),
            datetime(2023, 1, 1, 10, 20, tzinfo=UTC),
        )
        self.assertIsNone(self.importer._get_date(None))
        self.assertIsNone(self.importer._get_rating(None))
