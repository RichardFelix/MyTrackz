from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Max, Min, Prefetch, Q
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Book,
    Episode,
    Game,
    Item,
    Manga,
    Media,
    MediaManager,
    MediaTypes,
    Movie,
    Season,
    Sources,
)
from events.models import Event
from users.models import MediaStatusChoices

mock_path = Path(__file__).resolve().parent / "mock_data"


class ItemModel(TestCase):
    """Test case for the Item model."""

    def setUp(self):
        """Set up test data for Item model."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_item_creation(self):
        """Test the creation of an Item instance."""
        self.assertEqual(self.item.media_id, "1")
        self.assertEqual(self.item.media_type, MediaTypes.MOVIE.value)
        self.assertEqual(self.item.title, "Test Movie")
        self.assertEqual(self.item.image, "http://example.com/image.jpg")

    def test_item_str_representation(self):
        """Test the string representation of an Item."""
        self.assertEqual(str(self.item), "Test Movie")

    def test_item_with_season_and_episode(self):
        """Test the string representation of an Item with season and episode."""
        item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=1,
            episode_number=2,
        )
        self.assertEqual(str(item), "Test Show S1E2")


class MediaManagerTests(TestCase):
    """Test case for the MediaManager class."""

    def setUp(self):
        """Set up test data for MediaManager tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Enable all media types for the user
        for media_type in MediaTypes.values:
            setattr(self.user, f"{media_type.lower()}_enabled", True)
        self.user.save()

        # Create test items for different media types
        self.tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )

        self.movie_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/fightclub.jpg",
        )

        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/bebop.jpg",
        )

        self.game_item = Item.objects.create(
            media_id="1234",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Last of Us",
            image="http://example.com/tlou.jpg",
        )

        self.book_item = Item.objects.create(
            media_id="OL21733390M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="1984",
            image="http://example.com/1984.jpg",
        )

        self.manga_item = Item.objects.create(
            media_id="2",
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            title="Berserk",
            image="http://example.com/berserk.jpg",
        )

        # Create media objects
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=8,
        )

        self.movie = Movie.objects.create(
            item=self.movie_item,
            user=self.user,
            status=Media.Status.COMPLETED.value,
            score=9,
        )

        self.anime = Anime.objects.create(
            item=self.anime_item,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=10,
            progress=13,
        )

        self.game = Game.objects.create(
            item=self.game_item,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=7,
            progress=120,
        )

        self.book = Book.objects.create(
            item=self.book_item,
            user=self.user,
            status=Media.Status.PLANNING.value,
            score=0,
        )

        self.manga = Manga.objects.create(
            item=self.manga_item,
            user=self.user,
            status=Media.Status.REPEATING.value,
            score=10,
            progress=100,
        )

        # Create seasons and episodes for TV
        self.season1_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season1 = Season.objects.create(
            item=self.season1_item,
            related_tv=self.tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=8,
        )

        # Create episodes for season 1
        for i in range(1, 5):
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Friends S1E{i}",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )

            watched_episodes = 3
            if i <= watched_episodes:
                Episode.objects.create(
                    item=episode_item,
                    related_season=self.season1,
                    end_date=date(2023, 6, i),
                )

        # Create events for upcoming episodes
        for i in range(4, 7):
            # Create the actual Event object using the anime item
            Event.objects.create(
                item=self.anime_item,
                episode_number=i + 13,
                datetime=timezone.now() + timedelta(days=i),
                notification_sent=False,
            )

    def test_get_historical_models(self):
        """Test the get_historical_models method."""
        manager = MediaManager()
        historical_models = manager.get_historical_models()

        expected_models = [
            f"historical{media_type}" for media_type in MediaTypes.values
        ]
        self.assertEqual(historical_models, expected_models)

    def test_get_media_list_with_status_filter(self):
        """Test the get_media_list method with status filter."""
        manager = MediaManager()

        # Test with specific status filter
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=[Media.Status.IN_PROGRESS.value],
            sort_filter="score",
        )

        self.assertEqual(media_list.count(), 1)
        self.assertEqual(media_list.first(), self.anime)

        # Test with ALL status filter
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="score",
        )

        self.assertEqual(media_list.count(), 1)

    def test_get_media_list_with_search(self):
        """Test the get_media_list method with search parameter."""
        manager = MediaManager()

        # Test with search term that matches
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="score",
            search="Cowboy",
        )

        self.assertEqual(media_list.count(), 1)

        # Test with search term that doesn't match
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="score",
            search="Naruto",
        )

        self.assertEqual(media_list.count(), 0)

    def test_apply_prefetch_related(self):
        """Test the _apply_prefetch_related method."""
        manager = MediaManager()

        # Test with TV media type
        queryset = TV.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.TV.value,
        )

        # Verify prefetch_related was applied
        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 2)

        # Test with Season media type
        queryset = Season.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.SEASON.value,
        )

        # Verify prefetch_related was applied
        self.assertTrue(hasattr(prefetched_queryset, "_prefetch_related_lookups"))
        prefetch_lookups = prefetched_queryset._prefetch_related_lookups
        self.assertEqual(len(prefetch_lookups), 1)

        # Test with other media type
        queryset = Movie.objects.filter(user=self.user.id)
        prefetched_queryset = manager._apply_prefetch_related(
            queryset,
            MediaTypes.MOVIE.value,
        )

        # Verify no prefetch_related was applied
        self.assertEqual(prefetched_queryset, queryset)

    def test_get_media_list_with_prefetch_related(self):
        """Test the get_media_list method with prefetch_related for TV and Season."""
        manager = MediaManager()

        # Test with TV media type
        tv_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.TV.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="score",
        )

        # Force evaluation of the queryset and prefetch the related objects
        tv_list = list(tv_list)

        # Pre-load all the related seasons and episodes outside the assertion block
        for tv in tv_list:
            seasons = list(tv.seasons.all())
            for season in seasons:
                list(season.episodes.all())

        # Now verify no additional queries are made when accessing the prefetched data
        with self.assertNumQueries(0):  # No additional queries should be made
            for tv in tv_list:
                seasons = list(tv.seasons.all())
                for season in seasons:
                    list(season.episodes.all())

        # Test with Season media type
        season_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.SEASON.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="score",
        )

        # Force evaluation of the queryset and prefetch the related objects
        season_list = list(season_list)

        # Pre-load all the related episodes outside the assertion block
        for season in season_list:
            list(season.episodes.all())

        # Verify prefetch_related was applied (check if episodes are prefetched)
        with self.assertNumQueries(0):  # No additional queries should be made
            for season in season_list:
                list(season.episodes.all())

    def test_sort_by_property(self):
        """Test the _sort_by_property method."""
        manager = MediaManager()

        # Create seasons with different dates
        season2_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends Season 2",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        season2 = Season.objects.create(
            item=season2_item,
            related_tv=self.tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=7,
        )

        # Create episodes for season 2 with later dates
        for i in range(1, 3):
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Friends S2E{i}",
                image="http://example.com/image.jpg",
                season_number=2,
                episode_number=i,
            )

            Episode.objects.create(
                item=episode_item,
                related_season=season2,
                end_date=date(2023, 7, i),
            )

        # Create a season with no episodes (no dates)
        season3_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends Season 3",
            image="http://example.com/image.jpg",
            season_number=3,
        )

        season3 = Season.objects.create(
            item=season3_item,
            related_tv=self.tv,
            user=self.user,
            status=Media.Status.PLANNING.value,
            score=0,
        )

        # Get all seasons
        queryset = Season.objects.filter(user=self.user.id)

        # Test sorting by start_date
        sorted_list = manager._sort_by_property(queryset, "start_date")
        self.assertEqual(sorted_list, [self.season1, season2, season3])

        # Test sorting by end_date
        sorted_list = manager._sort_by_property(queryset, "end_date")
        self.assertEqual(sorted_list, [season2, self.season1, season3])

        # Test sorting by non-date property
        sorted_list = manager._sort_by_property(queryset, "score")
        # Should be sorted by score in descending order
        expected_order = sorted(
            [self.season1, season2, season3],
            key=lambda x: x.score,
            reverse=True,
        )
        self.assertEqual(sorted_list, expected_order)

    def test_get_media_list_sort_by_item_field(self):
        """Test the get_media_list method with sorting by item field."""
        manager = MediaManager()

        # Test sorting by title (ascending)
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.MOVIE.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="title",
        )

        # Should be sorted alphabetically
        self.assertEqual(media_list.first(), self.movie)

    def test_get_media_list_sort_by_regular_field(self):
        """Test the get_media_list method with sorting by regular field."""
        manager = MediaManager()

        # Create another anime with different score
        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        anime2 = Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=6,
        )

        # Test sorting by score (descending)
        media_list = manager.get_media_list(
            user=self.user,
            media_type=MediaTypes.ANIME.value,
            status_filter=[MediaStatusChoices.ALL],
            sort_filter="score",
        )

        # Higher score should come first
        self.assertEqual(list(media_list), [self.anime, anime2])

    def test_get_media_types_to_process(self):
        """Test the _get_media_types_to_process method."""
        manager = MediaManager()

        # Test with specific media type
        media_types = manager._get_media_types_to_process(
            self.user,
            MediaTypes.ANIME.value,
        )
        self.assertEqual(media_types, [MediaTypes.ANIME.value])

        # Test with no specific media type (all enabled)
        media_types = manager._get_media_types_to_process(self.user, None)

        # Should include all enabled media types except TV and Episode
        # Season should be first if TV is enabled
        self.assertIn(MediaTypes.SEASON.value, media_types)
        self.assertEqual(
            media_types[0],
            MediaTypes.SEASON.value,
        )  # Season should be first

        # Disable TV and season
        self.user.tv_enabled = False
        self.user.season_enabled = False
        self.user.save()

        # Test again with TV disabled
        media_types = manager._get_media_types_to_process(self.user, None)

        # Should not include season now
        self.assertNotIn(MediaTypes.SEASON.value, media_types)

    def test_sort_in_progress_media(self):
        """Test the _sort_in_progress_media method."""
        manager = MediaManager()

        # Get anime queryset
        queryset = Anime.objects.filter(user=self.user.id)

        # Add annotations
        now = timezone.now()
        queryset = queryset.annotate(
            max_progress=Max("item__event__episode_number"),
        )

        # Prefetch the next event for each media item
        queryset = queryset.prefetch_related(
            Prefetch(
                "item__event_set",
                queryset=Event.objects.filter(
                    datetime__gt=now,
                ).order_by("datetime"),
                to_attr="next_events",
            ),
        )

        for media in queryset:
            media.next_event = (
                media.item.next_events[0] if media.item.next_events else None
            )

        # Test sort by upcoming
        sorted_queryset = manager._sort_in_progress_media(
            queryset,
            "upcoming",
            MediaTypes.ANIME.value,
        )
        self.assertEqual(sorted_queryset[0], self.anime)

        # Test sort by title
        sorted_queryset = manager._sort_in_progress_media(
            queryset,
            "title",
            MediaTypes.ANIME.value,
        )
        self.assertEqual(sorted_queryset[0], self.anime)

        # Create another anime for testing completion and episodes_left
        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        anime2 = Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=8,
            progress=20,
        )

        # Add events for max_progress
        for i in range(1, 25):
            Event.objects.create(
                item=anime_item2,
                episode_number=i,
                datetime=timezone.now() - timedelta(days=i),
                notification_sent=True,
            )

        # Update queryset to include the new anime
        queryset = Anime.objects.filter(user=self.user.id)
        queryset = queryset.annotate(
            max_progress=Max("item__event__episode_number"),
            next_episode_number=Min(
                "item__event__episode_number",
                filter=Q(item__event__datetime__gt=now),
            ),
            next_episode_datetime=Min(
                "item__event__datetime",
                filter=Q(item__event__datetime__gt=now),
            ),
        )

        # Test sort by completion
        sorted_queryset = manager._sort_in_progress_media(
            queryset,
            "completion",
            MediaTypes.ANIME.value,
        )
        self.assertEqual(list(sorted_queryset), [self.anime, anime2])

        # Test sort by episodes_left
        sorted_queryset = manager._sort_in_progress_media(
            queryset,
            "episodes_left",
            MediaTypes.ANIME.value,
        )
        self.assertEqual(list(sorted_queryset), [self.anime, anime2])

    @patch("app.providers.services.get_media_metadata")
    def test_get_in_progress_all_media_types(self, mock_get_media_metadata):
        """Test the get_in_progress method with all media types."""
        # Mock the metadata service
        mock_get_media_metadata.return_value = {
            "related": {"seasons": [{"season_number": 1}]},
            "season/1": {"episodes": [{"episode_number": i} for i in range(1, 25)]},
            "max_progress": 24,
        }

        manager = MediaManager()

        # Test with all media types
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="title",
        )

        # Should include anime, game, and manga (in progress or repeating)
        self.assertIn(MediaTypes.ANIME.value, in_progress)
        self.assertIn(MediaTypes.GAME.value, in_progress)
        self.assertIn(MediaTypes.MANGA.value, in_progress)
        self.assertNotIn(MediaTypes.MOVIE.value, in_progress)  # Completed
        self.assertNotIn(MediaTypes.BOOK.value, in_progress)  # Planned

    @patch("app.providers.services.get_media_metadata")
    def test_get_in_progress_sort_by_upcoming(self, mock_get_media_metadata):
        """Test the get_in_progress method with sort_by='upcoming'."""
        # Mock the metadata service
        mock_get_media_metadata.return_value = {
            "related": {"seasons": [{"season_number": 1}]},
            "season/1": {"episodes": [{"episode_number": i} for i in range(1, 25)]},
            "max_progress": 24,
        }

        manager = MediaManager()

        # Test with sort_by='upcoming'
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="upcoming",
        )

        # Anime should be included because it has upcoming episodes
        self.assertIn(MediaTypes.ANIME.value, in_progress)
        self.assertEqual(len(in_progress[MediaTypes.ANIME.value]["items"]), 1)

    @patch("app.providers.services.get_media_metadata")
    def test_get_in_progress_sort_by_completion(self, mock_get_media_metadata):
        """Test the get_in_progress method with sort_by='completion'."""
        # Mock the metadata service
        mock_get_media_metadata.return_value = {
            "related": {"seasons": [{"season_number": 1}]},
            "season/1": {"episodes": [{"episode_number": i} for i in range(1, 25)]},
            "max_progress": 24,
        }

        manager = MediaManager()

        # Create another anime with different progress
        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        anime2 = Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=8,
            progress=20,  # Higher progress
        )

        # Add events for max_progress
        for i in range(1, 25):
            # Create the actual Event object
            Event.objects.create(
                item=anime_item2,
                episode_number=i,
                datetime=timezone.now() - timedelta(days=i),
                notification_sent=True,
            )

        # Test with sort_by='completion'
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="completion",
        )

        # Anime with higher completion rate should come first
        self.assertEqual(
            list(in_progress[MediaTypes.ANIME.value]["items"]),
            [anime2, self.anime],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_get_in_progress_sort_by_episodes_left(self, mock_get_media_metadata):
        """Test the get_in_progress method with sort_by='episodes_left'."""
        # Mock the metadata service
        mock_get_media_metadata.return_value = {
            "related": {"seasons": [{"season_number": 1}]},
            "season/1": {"episodes": [{"episode_number": i} for i in range(1, 25)]},
            "max_progress": 24,
        }

        manager = MediaManager()

        # Create another anime with different progress
        anime_item2 = Item.objects.create(
            media_id="5",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Naruto",
            image="http://example.com/naruto.jpg",
        )

        anime2 = Anime.objects.create(
            item=anime_item2,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            score=8,
            progress=20,  # Higher progress
        )

        # Add events for max_progress
        for i in range(1, 25):
            # Create the actual Event object
            Event.objects.create(
                item=anime_item2,
                episode_number=i,
                datetime=timezone.now() - timedelta(days=i),
                notification_sent=True,
            )

        # Test with sort_by='episodes_left'
        in_progress = manager.get_in_progress(
            user=self.user,
            sort_by="episodes_left",
        )

        # Anime with fewer episodes left should come first
        self.assertEqual(
            list(in_progress[MediaTypes.ANIME.value]["items"]),
            [anime2, self.anime],
        )

    def test_get_media(self):
        """Test the get_media method."""
        manager = MediaManager()

        # Test getting a TV show
        tv = manager.get_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.TV.value,
            source=Sources.TMDB.value,
        )

        self.assertEqual(tv, self.tv)

        # Test getting a season
        season = manager.get_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
            season_number=1,
        )

        self.assertEqual(season, self.season1)

        # Test getting an episode
        episode = manager.get_media(
            user=self.user,
            media_id="1668",
            media_type=MediaTypes.EPISODE.value,
            source=Sources.TMDB.value,
            season_number=1,
            episode_number=1,
        )

        self.assertIsNotNone(episode)
        self.assertEqual(episode.item.episode_number, 1)

        # Test getting a non-existent media
        non_existent = manager.get_media(
            user=self.user,
            media_id="9999",
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
        )

        self.assertIsNone(non_existent)


class MediaModel(TestCase):
    """Test the custom save of the Media model."""

    def setUp(self):
        """Create a user."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_anime = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/image.jpg",
        )

        self.anime = Anime.objects.create(
            item=item_anime,
            user=self.user,
            status=Media.Status.PLANNING.value,
        )

    def test_completed_no_end(self):
        """When completed, if not specified end_date, it should be the current date."""
        self.anime.status = Media.Status.COMPLETED.value
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).end_date,
            timezone.now().date(),
        )

    def test_completed_end(self):
        """When completed, if specified end_date, it should be the specified date."""
        self.anime.status = Media.Status.COMPLETED.value
        self.anime.end_date = "2023-06-01"
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).end_date,
            date(2023, 6, 1),
        )

    def test_completed_progress(self):
        """When completed, the progress should be the total number of episodes."""
        self.anime.status = Media.Status.COMPLETED.value
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).progress,
            26,
        )

    def test_completed_from_repeating(self):
        """When completed from repeating, repeats should be incremented."""
        self.anime.status = Media.Status.REPEATING.value
        self.anime.save()

        self.anime.status = Media.Status.COMPLETED.value
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).repeats,
            1,
        )

    def test_progress_is_max(self):
        """When progress is maximum number of episodes.

        Status should be completed and end_date the current date if not specified.
        """
        self.anime.status = Media.Status.IN_PROGRESS.value
        self.anime.progress = 26
        self.anime.save()

        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).status,
            Media.Status.COMPLETED.value,
        )
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).end_date,
            timezone.now().date(),
        )

    def test_progress_is_max_from_repeating(self):
        """When progress is maximum number of episodes and status is repeating.

        Repeat should be incremented.
        """
        self.anime.status = Media.Status.REPEATING.value
        self.anime.save()
        self.anime.progress = 26
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).repeats,
            1,
        )

    def test_progress_bigger_than_max(self):
        """When progress is bigger than max, it should be set to max."""
        self.anime.status = Media.Status.IN_PROGRESS.value
        self.anime.progress = 30
        self.anime.save()
        self.assertEqual(
            Anime.objects.get(item__media_id="1", user=self.user).progress,
            26,
        )


class TVModel(TestCase):
    """Test the @properties and custom save of the TV model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )

        self.tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            notes="",
        )

        item_season1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        # create first season
        season1 = Season.objects.create(
            item=item_season1,
            related_tv=self.tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

        item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=season1,
            end_date=date(2023, 6, 1),
        )

        item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=season1,
            end_date=date(2023, 6, 2),
        )

        item_season2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        # create second season
        season2 = Season.objects.create(
            item=item_season2,
            related_tv=self.tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

        item_ep3 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep3,
            related_season=season2,
            end_date=date(2023, 6, 4),
        )

        item_ep4 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep4,
            related_season=season2,
            end_date=date(2023, 6, 5),
        )

    def test_tv_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.tv.progress, 4)

    def test_tv_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(self.tv.start_date, date(2023, 6, 1))

    def test_tv_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(self.tv.end_date, date(2023, 6, 5))

    def test_tv_save(self):
        """Test the custom save method of the TV model."""
        self.tv.status = Media.Status.COMPLETED.value
        self.tv.save(update_fields=["status"])

        # check if all seasons are created with the status Media.Status.COMPLETED.value
        self.assertEqual(
            self.tv.seasons.filter(status=Media.Status.COMPLETED.value).count(),
            10,
        )


class SeasonModel(TestCase):
    """Test the @properties and custom save of the Season model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )

        related_tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            related_tv=related_tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

        item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=item_ep1,
            related_season=self.season,
            end_date=date(2023, 6, 1),
        )

        item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )
        Episode.objects.create(
            item=item_ep2,
            related_season=self.season,
            end_date=date(2023, 6, 2),
        )

    def test_season_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.season.progress, 2)

    def test_season_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(self.season.start_date, date(2023, 6, 1))

    def test_season_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(self.season.end_date, date(2023, 6, 2))

    def test_season_save(self):
        """Test the custom save method of the Season model."""
        self.season.status = Media.Status.COMPLETED.value
        self.season.save(update_fields=["status"])

        # check if all episodes are created
        self.assertEqual(self.season.episodes.count(), 24)

    @patch("app.models.Season.get_episode_item")
    def test_watch_method(self, mock_get_episode_item):
        """Test the watch method of the Season model."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Test watching a new episode
        self.season.watch(3, date(2023, 6, 3))

        # Check if the episode was created
        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episode.end_date, date(2023, 6, 3))
        self.assertEqual(episode.repeats, 0)

        # Test rewatching the same episode
        self.season.watch(3, date(2023, 6, 4))

        # Check if the episode was updated
        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episode.end_date, date(2023, 6, 4))
        self.assertEqual(episode.repeats, 1)

    @patch("app.models.Season.get_episode_item")
    def test_watch_with_none_date(self, mock_get_episode_item):
        """Test the watch method with None date."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Test watching with None date
        self.season.watch(3, "None")

        # Check if the episode was created with None date
        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertIsNone(episode.end_date)

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_method(self, mock_get_episode_item):
        """Test the unwatch method of the Season model."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Create an episode first
        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=date(2023, 6, 3),
        )

        # Test unwatching the episode
        self.season.unwatch(3)

        # Check if the episode was deleted
        with self.assertRaises(Episode.DoesNotExist):
            Episode.objects.get(
                related_season=self.season,
                item=episode_item,
            )

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_with_repeats(self, mock_get_episode_item):
        """Test the unwatch method with an episode that has repeats."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Create an episode with repeats
        Episode.objects.create(
            related_season=self.season,
            item=episode_item,
            end_date=date(2023, 6, 3),
            repeats=2,
        )

        # Test unwatching the episode
        self.season.unwatch(3)

        # Check if the episode's repeats were decreased
        episode = Episode.objects.get(
            related_season=self.season,
            item=episode_item,
        )
        self.assertEqual(episode.repeats, 1)

    @patch("app.models.Season.get_episode_item")
    def test_unwatch_nonexistent_episode(self, mock_get_episode_item):
        """Test unwatching a non-existent episode."""
        # Mock the get_episode_item method
        episode_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=3,
        )
        mock_get_episode_item.return_value = episode_item

        # Test unwatching a non-existent episode
        self.season.unwatch(3)

        # No exception should be raised, and no episode should be created
        with self.assertRaises(Episode.DoesNotExist):
            Episode.objects.get(
                related_season=self.season,
                item=episode_item,
            )


class EpisodeModel(TestCase):
    """Test the custom save of the Episode model."""

    def setUp(self):
        """Create a user and a season."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )

        related_tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            notes="",
            status=Media.Status.IN_PROGRESS.value,
        )

        item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season = Season.objects.create(
            item=item_season,
            related_tv=related_tv,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            notes="",
        )

    def test_episode_save(self):
        """Test the custom save method of the Episode model."""
        for i in range(1, 25):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=date(2023, 6, i),
            )

        # when all episodes are created, the season status should be COMPLETED
        self.assertEqual(self.season.status, Media.Status.COMPLETED.value)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_save_updates_season_status(self, mock_get_media_metadata):
        """Test that saving episodes updates the season status."""
        # Mock the metadata service
        mock_get_media_metadata.return_value = {
            "related": {
                "seasons": [{"season_number": 1}],
            },
            "season/1": {
                "episodes": [{"episode_number": i} for i in range(1, 5)],
            },
        }

        # Create episodes but not all of them
        for i in range(1, 3):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=date(2023, 6, i),
            )

        # Season should still be in progress
        self.assertEqual(self.season.status, Media.Status.IN_PROGRESS.value)

        # Add the remaining episodes
        for i in range(3, 5):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=date(2023, 6, i),
            )

        # Season should now be completed
        self.assertEqual(self.season.status, Media.Status.COMPLETED.value)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_save_with_repeats(self, mock_get_media_metadata):
        """Test that saving episodes with repeats correctly calculates total watches."""
        # Mock the metadata service
        mock_get_media_metadata.return_value = {
            "related": {
                "seasons": [{"season_number": 1}],
            },
            "season/1": {
                "episodes": [{"episode_number": i} for i in range(1, 3)],
            },
        }

        # Create episodes with repeats
        for i in range(1, 3):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            episode = Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=date(2023, 6, i),
            )

            # Add repeats to the first episode
            if i == 1:
                episode.repeats = 1
                episode.save()

        self.assertEqual(self.season.status, Media.Status.IN_PROGRESS.value)

        episode_2 = Episode.objects.get(
            item__media_id="1668",
            item__season_number=1,
            item__episode_number=2,
            related_season=self.season,
        )

        episode_2.repeats = 1
        episode_2.save()

        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Media.Status.COMPLETED.value)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_save_updates_tv_status(self, mock_get_media_metadata):
        """Test that completing the last season updates the TV status."""
        # Mock the metadata service to indicate this is the last season
        mock_get_media_metadata.return_value = {
            "related": {
                "seasons": [{"season_number": 1}],  # Only one season
            },
            "season/1": {
                "episodes": [{"episode_number": i} for i in range(1, 3)],
            },
        }

        # Create all episodes for the season
        for i in range(1, 3):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=date(2023, 6, i),
            )

        # Season should be completed
        self.assertEqual(self.season.status, Media.Status.COMPLETED.value)

        # TV show should also be completed since this was the last season
        self.assertEqual(self.season.related_tv.status, Media.Status.COMPLETED.value)

    @patch("app.providers.services.get_media_metadata")
    def test_episode_save_not_last_season(self, mock_get_media_metadata):
        """Test completing a season that is not the last one doesn't complete the TV."""
        # Mock the metadata service to indicate this is not the last season
        mock_get_media_metadata.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 1},
                    {"season_number": 2},
                ],  # Two seasons
            },
            "season/1": {
                "episodes": [{"episode_number": i} for i in range(1, 3)],
            },
        }

        # Create all episodes for the season
        for i in range(1, 3):
            item_episode = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Friends",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=item_episode,
                related_season=self.season,
                end_date=date(2023, 6, i),
            )

        # Season should be completed
        self.assertEqual(self.season.status, Media.Status.COMPLETED.value)

        # TV show should still be in progress since this was not the last season
        self.assertEqual(self.season.related_tv.status, Media.Status.IN_PROGRESS.value)


class GameModel(TestCase):
    """Test case for the Game model methods."""

    def setUp(self):
        """Set up test data for Game model tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.game_item = Item.objects.create(
            media_id="1234",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="The Last of Us",
            image="http://example.com/tlou.jpg",
        )

        self.game = Game.objects.create(
            item=self.game_item,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
            progress=60,  # 60 minutes
        )

    def test_increase_progress(self):
        """Test increasing the progress of a game."""
        initial_progress = self.game.progress
        self.game.increase_progress()

        # Progress should be increased by 30 minutes
        self.assertEqual(self.game.progress, initial_progress + 30)

    def test_decrease_progress(self):
        """Test decreasing the progress of a game."""
        initial_progress = self.game.progress
        self.game.decrease_progress()

        # Progress should be decreased by 30 minutes
        self.assertEqual(self.game.progress, initial_progress - 30)

    def test_field_tracker(self):
        """Test that the field tracker is tracking changes."""
        # Initially, there should be no changes
        self.assertFalse(self.game.tracker.changed())

        # Change the progress
        self.game.progress = 90

        # Now there should be changes
        self.assertTrue(self.game.tracker.changed())
        self.assertEqual(self.game.tracker.previous("progress"), 60)

    def test_multiple_progress_changes(self):
        """Test multiple progress changes."""
        # Increase progress twice
        self.game.increase_progress()
        self.game.increase_progress()

        # Progress should be increased by 60 minutes total
        self.assertEqual(self.game.progress, 120)

        # Decrease progress once
        self.game.decrease_progress()

        # Progress should now be 90 minutes
        self.assertEqual(self.game.progress, 90)
