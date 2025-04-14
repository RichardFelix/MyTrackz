import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import TV, Anime, Item, Manga, Media, MediaTypes, Movie, Season, Sources
from events.models import Event


class EventModelTests(TestCase):
    """Test the Event model."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create test items
        self.tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )

        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
        )

        self.manga_item = Item.objects.create(
            media_id="66296374554",
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
        )

        # Create media objects
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Media.Status.IN_PROGRESS.value,
        )

        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            related_tv=self.tv,
            status=Media.Status.IN_PROGRESS.value,
        )

        self.movie = Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Media.Status.PLANNING.value,
        )

        self.anime = Anime.objects.create(
            user=self.user,
            item=self.anime_item,
            status=Media.Status.IN_PROGRESS.value,
        )

        self.manga = Manga.objects.create(
            user=self.user,
            item=self.manga_item,
            status=Media.Status.IN_PROGRESS.value,
        )

        # Create events
        self.now = timezone.now()
        self.tomorrow = self.now + datetime.timedelta(days=1)
        self.next_week = self.now + datetime.timedelta(days=7)

        self.tv_event = Event.objects.create(
            item=self.tv_item,
            datetime=self.tomorrow,
        )

        self.season_event = Event.objects.create(
            item=self.season_item,
            episode_number=1,
            datetime=self.tomorrow,
        )

        self.movie_event = Event.objects.create(
            item=self.movie_item,
            datetime=self.next_week,
        )

        self.anime_event = Event.objects.create(
            item=self.anime_item,
            episode_number=1,
            datetime=self.tomorrow,
        )

        self.manga_event = Event.objects.create(
            item=self.manga_item,
            episode_number=1,
            datetime=self.tomorrow,
        )

    def test_event_string_representation(self):
        """Test the string representation of events."""
        # TV show event
        self.assertEqual(str(self.tv_event), "Test TV Show")

        # Season event
        self.assertEqual(
            str(self.season_event),
            "Test TV Show S1 - Ep. 1",
        )

        # Movie event
        self.assertEqual(str(self.movie_event), "Test Movie")

        # Anime event
        self.assertEqual(str(self.anime_event), "Test Anime - Ep. 1")

        # Manga event
        self.assertEqual(str(self.manga_event), "Test Manga - Ch. 1")

    def test_readable_episode_number(self):
        """Test the readable_episode_number property."""
        # Event with no episode number
        self.assertEqual(self.tv_event.readable_episode_number, "")

        # Season event
        self.assertEqual(self.season_event.readable_episode_number, "Ep. 1")

        # Anime event
        self.assertEqual(self.anime_event.readable_episode_number, "Ep. 1")

        # Manga event
        self.assertEqual(self.manga_event.readable_episode_number, "Ch. 1")


class EventManagerTests(TestCase):
    """Test the EventManager custom manager."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.credentials_other = {"username": "otheruser", "password": "testpassword"}
        self.other_user = get_user_model().objects.create_user(**self.credentials_other)

        # Create test items
        self.tv_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV Show",
        )

        self.season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            season_number=1,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
        )

        self.paused_movie_item = Item.objects.create(
            media_id="278",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Paused Movie",
        )

        self.dropped_movie_item = Item.objects.create(
            media_id="424",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Dropped Movie",
        )

        self.manga_item = Item.objects.create(
            media_id="66296374554",
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            title="Test Manga",
        )

        # Create media objects
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Media.Status.IN_PROGRESS.value,
        )

        self.other_tv = TV.objects.create(
            user=self.other_user,
            item=self.tv_item,
            status=Media.Status.IN_PROGRESS.value,
        )

        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            related_tv=self.tv,
            status=Media.Status.IN_PROGRESS.value,
        )

        self.movie = Movie.objects.create(
            user=self.user,
            item=self.movie_item,
            status=Media.Status.PLANNING.value,
        )

        self.paused_movie = Movie.objects.create(
            user=self.user,
            item=self.paused_movie_item,
            status=Media.Status.PAUSED.value,
        )

        self.dropped_movie = Movie.objects.create(
            user=self.user,
            item=self.dropped_movie_item,
            status=Media.Status.DROPPED.value,
        )

        self.manga = Manga.objects.create(
            user=self.user,
            item=self.manga_item,
            status=Media.Status.IN_PROGRESS.value,
        )

        # Use fixed dates instead of timezone.now()
        # Base date: April 15, 2025 at noon UTC
        self.base_date = datetime.datetime(2025, 4, 15, 12, 0, 0, tzinfo=datetime.UTC)
        self.yesterday = self.base_date - datetime.timedelta(days=1)  # April 14
        self.tomorrow = self.base_date + datetime.timedelta(days=1)  # April 16
        self.next_week = self.base_date + datetime.timedelta(days=7)  # April 22

        # Create events with fixed dates
        self.tv_event = Event.objects.create(
            item=self.tv_item,
            datetime=self.tomorrow,  # April 16
        )

        self.past_event = Event.objects.create(
            item=self.season_item,
            episode_number=1,
            datetime=self.yesterday,  # April 14
        )

        self.movie_event = Event.objects.create(
            item=self.movie_item,
            datetime=self.next_week,  # April 22
        )

        self.paused_movie_event = Event.objects.create(
            item=self.paused_movie_item,
            datetime=self.next_week,  # April 22
        )

        self.dropped_movie_event = Event.objects.create(
            item=self.dropped_movie_item,
            datetime=self.next_week,  # April 22
        )

        self.season_event = Event.objects.create(
            item=self.season_item,
            episode_number=2,
            datetime=self.tomorrow,  # April 16
        )

        # Manga with multiple events
        self.manga_event1 = Event.objects.create(
            item=self.manga_item,
            episode_number=1,
            datetime=self.tomorrow,  # April 16
        )

        self.manga_event2 = Event.objects.create(
            item=self.manga_item,
            episode_number=2,
            datetime=self.next_week,  # April 22
        )

    def test_get_user_events(self):
        """Test the get_user_events method."""
        # Use fixed dates for testing
        today = self.base_date.date()  # April 15
        next_week = today + datetime.timedelta(days=7)  # April 22

        # Get events for the user
        events = Event.objects.get_user_events(self.user, today, next_week)

        # Should include TV, season, movie events but not past events
        self.assertEqual(events.count(), 5)
        self.assertIn(self.tv_event, events)
        self.assertIn(self.season_event, events)
        self.assertIn(self.manga_event1, events)
        self.assertIn(self.movie_event, events)
        self.assertIn(self.manga_event2, events)
        self.assertNotIn(self.past_event, events)

        # Get events for the other user
        other_events = Event.objects.get_user_events(self.other_user, today, next_week)

        # Should only include the TV event (shared item)
        self.assertEqual(other_events.count(), 1)
        self.assertIn(self.tv_event, other_events)

        # Test with a different date range
        yesterday = today - datetime.timedelta(days=1)  # April 14
        tomorrow = today + datetime.timedelta(days=1)  # April 16
        limited_events = Event.objects.get_user_events(self.user, yesterday, tomorrow)

        self.assertEqual(limited_events.count(), 4)
        self.assertIn(self.past_event, limited_events)
        self.assertIn(self.tv_event, limited_events)
        self.assertIn(self.season_event, limited_events)
        self.assertIn(self.manga_event1, limited_events)
        self.assertNotIn(self.movie_event, limited_events)  # Next week, outside range
