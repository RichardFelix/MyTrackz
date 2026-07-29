from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app.models import TV, Episode, Item, MediaTypes, Season, Sources, Status
from events.calendar.main import cleanup_invalid_events, fetch_releases, save_events
from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin


class CalendarMainTests(CalendarFixturesMixin, TestCase):
    """Test the calendar orchestration entrypoint."""

    @patch("events.calendar.selectors.tmdb.movie_changes")
    @patch("events.calendar.selectors.tmdb.tv_changes")
    @patch("events.calendar.main.process_comic")
    @patch("events.calendar.main.process_tv")
    @patch("events.calendar.main.process_other")
    @patch("events.calendar.main.process_anime_bulk")
    def test_fetch_releases_all_types(
        self,
        mock_process_anime_bulk,
        mock_process_other,
        mock_process_tv,
        mock_process_comic,
        mock_movie_changes,
        mock_tv_changes,
    ):
        """Test fetch_releases with all media types."""
        mock_tv_changes.return_value = set()
        mock_movie_changes.return_value = set()

        mock_process_tv.side_effect = lambda _, events_bulk: events_bulk.append(
            Event(
                item=self.season_item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_other.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_comic.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )
        mock_process_anime_bulk.side_effect = lambda items, events_bulk: [
            events_bulk.append(
                Event(
                    item=item,
                    content_number=1,
                    datetime=timezone.now(),
                ),
            )
            for item in items
        ]

        result = fetch_releases(self.user.id)

        mock_process_anime_bulk.assert_called_once()
        anime_items = mock_process_anime_bulk.call_args[0][0]
        self.assertEqual(len(anime_items), 1)
        self.assertEqual(anime_items[0].id, self.anime_item.id)

        self.assertTrue(Event.objects.filter(item=self.season_item).exists())
        self.assertEqual(mock_process_other.call_count, 3)

        self.assertTrue(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertTrue(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())
        self.assertTrue(Event.objects.filter(item=self.comic_item).exists())

        self.assertIn("Perfect Blue", result)
        self.assertIn("The Godfather", result)
        self.assertIn("Breaking Bad", result)
        self.assertIn("Berserk", result)
        self.assertIn("1984", result)

    @patch("events.calendar.main.process_other")
    def test_fetch_releases_specific_items(self, mock_process_other):
        """Test fetch_releases with specific items to process."""
        mock_process_other.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                content_number=1,
                datetime=timezone.now(),
            ),
        )

        items_to_process = [self.movie_item, self.book_item]
        result = fetch_releases(self.user.id, items_to_process)

        self.assertEqual(mock_process_other.call_count, 2)

        self.assertFalse(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertFalse(Event.objects.filter(item=self.season_item).exists())
        self.assertFalse(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())

        self.assertIn("The Godfather", result)
        self.assertIn("1984", result)
        self.assertNotIn("Perfect Blue", result)
        self.assertNotIn("Breaking Bad", result)
        self.assertNotIn("Berserk", result)

    def test_fetch_releases_returns_for_manual_items(self):
        """Manual items should be rejected before any processing."""
        manual_item = Item(source=Sources.MANUAL.value)

        result = fetch_releases(items_to_process=[manual_item])

        self.assertEqual(result, "Manual sources are not processed")

    @patch("events.calendar.main.get_items_to_process")
    def test_fetch_releases_returns_when_no_items_to_process(
        self,
        mock_get_items_to_process,
    ):
        """The task should return early when nothing is eligible."""
        mock_get_items_to_process.return_value = []

        result = fetch_releases(self.user.id)

        self.assertEqual(result, "No items to process")

    @patch("events.calendar.main.get_items_to_process")
    def test_fetch_releases_repairs_tv_when_no_items_need_provider_refresh(
        self,
        mock_get_items_to_process,
    ):
        """Existing future events should reconcile even without provider work."""
        mock_get_items_to_process.return_value = []
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        Season.objects.filter(
            item=self.season_item,
            user=self.user,
        ).update(status=Status.COMPLETED.value)
        season_four_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image="http://example.com/season4.jpg",
            season_number=4,
        )
        Event.objects.create(
            item=season_four_item,
            content_number=1,
            datetime=timezone.now() + timezone.timedelta(hours=4),
        )

        result = fetch_releases(self.user.id)

        self.assertEqual(result, "No items to process")
        tv.refresh_from_db()
        season_four = Season.objects.get(item=season_four_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(season_four.status, Status.PLANNING.value)

    def test_save_events_updates_existing_unnumbered_event(self):
        """Existing unnumbered events should be updated in place."""
        original_datetime = timezone.now() - timezone.timedelta(days=2)
        updated_datetime = timezone.now() + timezone.timedelta(days=2)
        Event.objects.create(
            item=self.movie_item,
            content_number=None,
            datetime=original_datetime,
        )

        _items_updated, created_events = save_events(
            [
                Event(
                    item=self.movie_item,
                    content_number=None,
                    datetime=updated_datetime,
                ),
            ],
        )

        self.assertEqual(created_events, [])

        self.assertEqual(
            Event.objects.filter(item=self.movie_item).count(),
            1,
        )
        self.assertEqual(
            Event.objects.get(item=self.movie_item).datetime,
            updated_datetime,
        )

    @patch("events.calendar.main.process_tv")
    def test_new_episode_reopens_completed_existing_season_once(
        self,
        mock_process_tv,
    ):
        """Only a newly inserted episode should reopen completed TV tracking."""
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        season = Season.objects.get(item=self.season_item, user=self.user)
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        Season.objects.filter(pk=season.pk).update(status=Status.COMPLETED.value)
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=timezone.now() - timezone.timedelta(days=7),
        )
        tv_history_count = tv.history.count()
        season_history_count = season.history.count()

        def add_second_episode(_item, events_bulk):
            events_bulk.extend(
                [
                    Event(
                        item=self.season_item,
                        content_number=1,
                        datetime=timezone.now() - timezone.timedelta(days=7),
                    ),
                    Event(
                        item=self.season_item,
                        content_number=2,
                        datetime=timezone.now() + timezone.timedelta(days=1),
                    ),
                ],
            )

        mock_process_tv.side_effect = add_second_episode

        fetch_releases(self.user, items_to_process=[self.tv_item])

        tv.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(season.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.history.count(), tv_history_count + 1)
        self.assertEqual(season.history.count(), season_history_count + 1)

        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        Season.objects.filter(pk=season.pk).update(status=Status.COMPLETED.value)
        fetch_releases(self.user, items_to_process=[self.tv_item])

        tv.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(season.status, Status.COMPLETED.value)

    @patch("events.calendar.main.process_tv")
    def test_new_already_watched_episode_does_not_reopen_completed_tv(
        self,
        mock_process_tv,
    ):
        """A delayed calendar event must not reopen an episode already watched."""
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        season = Season.objects.get(item=self.season_item, user=self.user)
        episode_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.EPISODE.value,
            title=self.tv_item.title,
            image=self.tv_item.image,
            season_number=self.season_item.season_number,
            episode_number=2,
        )
        Episode.objects.bulk_create(
            [Episode(item=episode_item, related_season=season)],
        )
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        Season.objects.filter(pk=season.pk).update(status=Status.COMPLETED.value)
        mock_process_tv.side_effect = lambda _item, events_bulk: events_bulk.append(
            Event(
                item=self.season_item,
                content_number=2,
                datetime=timezone.now() - timezone.timedelta(days=1),
            ),
        )

        fetch_releases(self.user, items_to_process=[self.tv_item])

        tv.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertEqual(season.status, Status.COMPLETED.value)

    def test_cleanup_invalid_events_removes_missing_numbered_events(self):
        """Stale numbered events should be removed after a refresh."""
        kept_datetime = timezone.now()
        removed_datetime = timezone.now() + timezone.timedelta(days=1)
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=kept_datetime,
        )
        Event.objects.create(
            item=self.season_item,
            content_number=2,
            datetime=removed_datetime,
        )

        cleanup_invalid_events(
            [
                Event(
                    item=self.season_item,
                    content_number=1,
                    datetime=kept_datetime,
                ),
            ],
        )

        self.assertTrue(
            Event.objects.filter(item=self.season_item, content_number=1).exists(),
        )
        self.assertFalse(
            Event.objects.filter(item=self.season_item, content_number=2).exists(),
        )

    @patch("events.calendar.main.process_other")
    def test_fetch_releases_returns_no_updates_message(self, mock_process_other):
        """The final message should mention when nothing was changed."""
        result = fetch_releases(items_to_process=[self.movie_item])

        mock_process_other.assert_called_once()
        self.assertIn("No releases have been updated.", result)
