from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Book, Item, MediaTypes, Movie, Sources, Status
from app.tasks import refresh_discover_feeds


class DiscoverViewTests(TestCase):
    """Test the discover page and its recommendation feed."""

    def setUp(self):
        """Create a user and log in."""
        # The feed cache is keyed per user id and fakeredis state persists
        # across tests in the same process, so start every test cold.
        cache.clear()

        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)
        self.mock_get_media_metadata.return_value = {"max_progress": 1}

    def _get_feed(self):
        """Return the rendered feed.

        The first GET is a cache miss that queues the build (which runs
        eagerly in tests) and returns the loading fragment; the second GET
        serves the computed feed from the cache.
        """
        self.client.get(reverse("discover_recommendations"))
        return self.client.get(reverse("discover_recommendations"))

    def _complete_movie(self, media_id, title, score=None):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/image.jpg",
        )
        return Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
            score=score,
        )

    def _complete_tv(self, media_id, title, score=None):
        # TV.end_date is a derived property (from its seasons/episodes), not a
        # settable field, unlike other media types. Completing a TV show also
        # fetches its seasons to auto-complete them, so the mock needs that shape.
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=title,
            image="http://example.com/image.jpg",
        )
        previous_return_value = self.mock_get_media_metadata.return_value
        self.mock_get_media_metadata.return_value = {
            "max_progress": 1,
            "related": {"seasons": []},
        }
        try:
            tv = TV.objects.create(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                score=score,
            )
        finally:
            self.mock_get_media_metadata.return_value = previous_return_value
        return tv

    def _in_progress_movie(self, media_id, title, score=None):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=title,
            image="http://example.com/image.jpg",
        )
        return Movie.objects.create(
            item=item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            score=score,
        )

    def _complete_book(self, media_id, title, score=None):
        item = Item.objects.create(
            media_id=media_id,
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title=title,
            image="http://example.com/image.jpg",
        )
        return Book.objects.create(
            item=item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
            score=score,
        )

    def test_discover_page_renders(self):
        """The discover page shell should render without loading the feed."""
        response = self.client.get(reverse("discover"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/discover.html")
        self.mock_get_media_metadata.assert_not_called()

    def test_shows_untracked_recommendation(self):
        """A recommended entry not yet tracked by the user should be surfaced."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {
                "recommendations": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Recommended Movie",
                        "image": "http://example.com/rec.jpg",
                    },
                ],
            },
        }

        response = self._get_feed()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/discover_recommendations.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertIn("Recommended Movie", titles)

    def test_hides_already_tracked_recommendation(self):
        """A recommended entry the user already tracks should not be suggested."""
        self._complete_movie("1", "Finished Movie")
        tracked_item = Item.objects.create(
            media_id="20",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Recommended Movie",
            image="http://example.com/rec.jpg",
        )
        Movie.objects.create(
            item=tracked_item, user=self.user, status=Status.PLANNING.value
        )

        self.mock_get_media_metadata.return_value = {
            "related": {
                "recommendations": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Recommended Movie",
                        "image": "http://example.com/rec.jpg",
                    },
                ],
            },
        }

        response = self._get_feed()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])

    def test_ranks_by_recommendation_frequency(self):
        """A title recommended by more completed items should rank higher."""
        self._complete_movie("1", "Movie One", score=9)
        self._complete_movie("2", "Movie Two", score=8)
        self._complete_movie("3", "Movie Three", score=7)

        shared = {
            "media_id": "500",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Shared Pick",
            "image": "http://example.com/shared.jpg",
        }
        rare = {
            "media_id": "600",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Rare Pick",
            "image": "http://example.com/rare.jpg",
        }

        def side_effect(media_type, media_id, _source, **_kwargs):  # noqa: ARG001
            recommendations_by_seed = {
                "1": [shared],
                "2": [shared],
                "3": [rare],
            }
            return {
                "related": {"recommendations": recommendations_by_seed[media_id]},
            }

        self.mock_get_media_metadata.side_effect = side_effect

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["Shared Pick", "Rare Pick"])

    @patch("app.helpers.DISCOVER_MAX_ITEMS_PER_TYPE", 1)
    def test_each_type_has_its_own_budget_not_a_shared_one(self):
        """A type with more picks (TV) doesn't shrink another type's own budget.

        Movies keep their single pick even though TV has more distinct
        recommendations available; each type is capped independently.
        """
        self._complete_movie("1", "Solo Movie")
        self._complete_tv("2", "TV Show One")
        self._complete_tv("3", "TV Show Two")
        self._complete_tv("4", "TV Show Three")

        movie_pick = {
            "media_id": "100",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": "Only Movie Pick",
            "image": "http://example.com/movie.jpg",
        }
        tv_pick_a = {
            "media_id": "200",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Popular TV Pick A",
            "image": "http://example.com/tv-a.jpg",
        }
        tv_pick_b = {
            "media_id": "201",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "title": "Popular TV Pick B",
            "image": "http://example.com/tv-b.jpg",
        }

        def side_effect(media_type, _media_id, _source, **_kwargs):
            if media_type == MediaTypes.MOVIE.value:
                return {"related": {"recommendations": [movie_pick]}}
            # Every TV seed recommends both TV picks, so TV has two distinct
            # recommendations available versus the movie's one.
            return {"related": {"recommendations": [tv_pick_a, tv_pick_b]}}

        self.mock_get_media_metadata.side_effect = side_effect

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(len(titles), 2)
        self.assertIn("Only Movie Pick", titles)

    def test_no_suggestions_shows_empty_state(self):
        """The page should show a helpful empty state when there's nothing to show."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {"related": {}}

        response = self._get_feed()

        self.assertEqual(response.context["suggestions"], [])
        self.assertContains(response, "Nothing to recommend yet")

    def test_shows_recommendation_seeded_from_completed_tv_show(self):
        """A completed TV show should also seed recommendations (regression test)."""
        self._complete_tv("1", "Finished TV Show")
        self.mock_get_media_metadata.return_value = {
            "related": {
                "recommendations": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.TV.value,
                        "title": "Recommended TV Show",
                        "image": "http://example.com/rec.jpg",
                    },
                ],
            },
        }

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertIn("Recommended TV Show", titles)

    def test_shows_book_series_sibling_as_a_recommendation(self):
        """Books fall back to series-sibling data (regression test).

        Books have no real "recommendations" endpoint (Hardcover/OpenLibrary), so
        Discover should fall back to their series-sibling data instead of
        omitting books entirely.
        """
        self._complete_book("1", "Series Book One")
        self.mock_get_media_metadata.return_value = {
            "related": {
                "Test Series": [
                    {
                        "media_id": "2",
                        "source": Sources.HARDCOVER.value,
                        "media_type": MediaTypes.BOOK.value,
                        "title": "Series Book Two",
                        "image": "http://example.com/book2.jpg",
                    },
                ],
            },
        }

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertIn("Series Book Two", titles)
        media_types = dict(response.context["media_type_counts"])
        self.assertIn(MediaTypes.BOOK.value, media_types)

    def test_seeds_from_in_progress_media_too(self):
        """An in-progress (not just completed) item should also seed recommendations."""
        self._in_progress_movie("1", "Watching Now")
        self.mock_get_media_metadata.return_value = {
            "related": {
                "recommendations": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Recommended From In Progress",
                        "image": "http://example.com/rec.jpg",
                    },
                ],
            },
        }

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertIn("Recommended From In Progress", titles)

    def test_media_type_counts_reflect_suggestion_breakdown(self):
        """The filter context should report an accurate per-type breakdown."""
        self._complete_movie("1", "Finished Movie")
        self._complete_tv("2", "Finished TV Show")

        def side_effect(media_type, media_id, _source, **_kwargs):  # noqa: ARG001
            if media_type == MediaTypes.TV.value:
                return {
                    "related": {
                        "recommendations": [
                            {
                                "media_id": "20",
                                "source": Sources.TMDB.value,
                                "media_type": MediaTypes.TV.value,
                                "title": "Recommended TV Show",
                                "image": "http://example.com/tv.jpg",
                            },
                        ],
                    },
                }
            return {
                "related": {
                    "recommendations": [
                        {
                            "media_id": "30",
                            "source": Sources.TMDB.value,
                            "media_type": MediaTypes.MOVIE.value,
                            "title": "Recommended Movie",
                            "image": "http://example.com/movie.jpg",
                        },
                    ],
                },
            }

        self.mock_get_media_metadata.side_effect = side_effect

        response = self._get_feed()

        self.assertEqual(
            response.context["media_type_counts"],
            [(MediaTypes.MOVIE.value, 1), (MediaTypes.TV.value, 1)],
        )
        self.assertContains(response, "TV Shows (1)")

    def test_degrades_gracefully_on_provider_network_error(self):
        """A network failure fetching recommendations shouldn't break the feed."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.side_effect = requests.exceptions.ConnectionError

        response = self._get_feed()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])

    def _recommendation(self, media_id, title):
        return {
            "media_id": media_id,
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "title": title,
            "image": f"http://example.com/{media_id}.jpg",
        }

    def test_cold_load_shows_building_state(self):
        """A cache miss should return the self-polling loading fragment."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("20", "Rec")]},
        }

        response = self.client.get(reverse("discover_recommendations"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/discover_loading.html")
        self.assertContains(response, "Building your recommendations")
        self.assertContains(response, reverse("discover_recommendations"))

    def test_warm_load_makes_no_provider_calls(self):
        """A cached feed must render without touching any provider."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("20", "Rec")]},
        }
        self._get_feed()

        self.mock_get_media_metadata.reset_mock()
        self.mock_get_media_metadata.side_effect = AssertionError(
            "provider called on the warm path"
        )
        response = self.client.get(reverse("discover_recommendations"))

        self.assertTemplateUsed(
            response, "app/components/discover_recommendations.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["Rec"])
        self.mock_get_media_metadata.assert_not_called()

    def test_tracking_a_suggestion_hides_it_from_the_cached_feed(self):
        """Tracking a suggested item removes it from the feed without a rebuild."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("20", "Rec")]},
        }
        self._get_feed()

        suggested_item = Item.objects.get(
            media_id="20", media_type=MediaTypes.MOVIE.value
        )
        Movie.objects.create(
            item=suggested_item, user=self.user, status=Status.PLANNING.value
        )

        response = self.client.get(reverse("discover_recommendations"))

        self.assertEqual(response.context["suggestions"], [])

    def test_cached_feed_reresolves_image_urls(self):
        """Image URLs come from the Item row at render time, not compute time."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("20", "Rec")]},
        }
        self._get_feed()

        suggested_item = Item.objects.get(
            media_id="20", media_type=MediaTypes.MOVIE.value
        )
        suggested_item.image_cached = True
        suggested_item.save(update_fields=["image_cached"])

        response = self.client.get(reverse("discover_recommendations"))

        image = response.context["suggestions"][0]["item"]["image"]
        self.assertEqual(image, suggested_item.cached_image_url)

    def test_refresh_endpoint_invalidates_and_rebuilds(self):
        """The refresh endpoint drops the cached feed and serves fresh data."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("20", "Old Rec")]},
        }
        self._get_feed()

        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("30", "New Rec")]},
        }
        response = self.client.post(reverse("discover_refresh"))

        self.assertTemplateUsed(response, "app/components/discover_loading.html")

        response = self.client.get(reverse("discover_recommendations"))
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["New Rec"])

    def test_refresh_discover_feeds_task_precomputes_for_users(self):
        """The beat task fills the cache so the first page view is already warm."""
        self._complete_movie("1", "Finished Movie")
        self.mock_get_media_metadata.return_value = {
            "related": {"recommendations": [self._recommendation("20", "Rec")]},
        }

        refresh_discover_feeds()

        response = self.client.get(reverse("discover_recommendations"))

        self.assertTemplateUsed(
            response, "app/components/discover_recommendations.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["Rec"])

    def test_polling_does_not_queue_duplicate_builds(self):
        """While a build is pending, further polls must not queue more builds."""
        self._complete_movie("1", "Finished Movie")

        with patch("app.views.compute_discover_feed.delay") as mock_delay:
            first = self.client.get(reverse("discover_recommendations"))
            second = self.client.get(reverse("discover_recommendations"))

        self.assertTemplateUsed(first, "app/components/discover_loading.html")
        self.assertTemplateUsed(second, "app/components/discover_loading.html")
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.args[0], self.user.id)

    def test_zero_media_user_gets_empty_state_not_endless_spinner(self):
        """An empty computed feed renders the empty state, not another spinner."""
        response = self._get_feed()

        self.assertTemplateUsed(
            response, "app/components/discover_recommendations.html"
        )
        self.assertContains(response, "Nothing to recommend yet")

    def test_beat_build_does_not_clear_a_refresh_pending_marker(self):
        """A build queued without ownership must not wipe a refresh's marker.

        Regression test for the refresh race: the daily beat fan-out queues
        builds with no build_id; if such a build cleared the pending marker
        set by a user-triggered refresh, polling could queue duplicate builds
        while the refresh's own build is still in flight.
        """
        from app.tasks import compute_discover_feed  # noqa: PLC0415

        pending_key = "discover_feed_pending_v1_" + str(self.user.id)
        cache.set(pending_key, "refresh-build-id", 600)

        # Beat-style build: no build_id -> owns no marker.
        compute_discover_feed(self.user.id)

        self.assertEqual(cache.get(pending_key), "refresh-build-id")

    def test_owning_build_clears_its_own_pending_marker(self):
        """A build queued with its marker's build_id clears the marker."""
        from app.tasks import compute_discover_feed  # noqa: PLC0415

        pending_key = "discover_feed_pending_v1_" + str(self.user.id)
        cache.set(pending_key, "my-build-id", 600)

        compute_discover_feed(self.user.id, "my-build-id")

        self.assertIsNone(cache.get(pending_key))

    def test_refresh_takes_over_a_stale_pending_marker(self):
        """Refresh must queue a rebuild even when a pending marker lingers."""
        pending_key = "discover_feed_pending_v1_" + str(self.user.id)
        cache.set(pending_key, "old-abandoned-build", 600)

        with patch("app.views.compute_discover_feed.delay") as mock_delay:
            self.client.post(reverse("discover_refresh"))

        mock_delay.assert_called_once()
        # The marker now belongs to the refresh's build.
        self.assertEqual(cache.get(pending_key), mock_delay.call_args.args[1])
