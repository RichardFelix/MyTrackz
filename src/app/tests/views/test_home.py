from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Anime,
    BasicMedia,
    Episode,
    Game,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from events.models import Event
from users.models import HomeLayoutChoices, HomeSortChoices


class HomeViewTests(TestCase):
    """Test the home view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        # Most tests in this class cover the compact-list presentation.
        self.user.home_layout = HomeLayoutChoices.LIST
        self.user.save(update_fields=["home_layout"])
        self.client.login(**self.credentials)
        self.metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)

        def mock_get_media_metadata(
            media_type,
            _media_id,
            _source,
            season_numbers=None,
            _episode_number=None,
        ):
            if media_type == MediaTypes.TV.value:
                return {
                    "title": "Test TV Show",
                    "image": "http://example.com/image.jpg",
                    "details": {"seasons": 1},
                    "related": {
                        "seasons": [
                            {
                                "season_number": 1,
                                "image": "http://example.com/image.jpg",
                            },
                        ],
                    },
                }

            if media_type == "tv_with_seasons":
                season_number = season_numbers[0]
                return {
                    "title": "Test TV Show",
                    "image": "http://example.com/image.jpg",
                    "details": {"seasons": 1},
                    f"season/{season_number}": {
                        "episodes": [{"id": i} for i in range(1, 11)],
                    },
                    "related": {
                        "seasons": [
                            {
                                "season_number": season_number,
                                "image": "http://example.com/image.jpg",
                            },
                        ],
                    },
                }

            if media_type == MediaTypes.SEASON.value:
                return {
                    "title": "Test TV Show",
                    "image": "http://example.com/image.jpg",
                    "max_progress": 10,
                    "season/1": {
                        "episodes": [{"id": i} for i in range(1, 11)],
                    },
                }

            if media_type == MediaTypes.ANIME.value:
                return {
                    "title": "Test Anime",
                    "image": "http://example.com/image.jpg",
                    "max_progress": 24,
                }

            if media_type == MediaTypes.MOVIE.value:
                return {
                    "title": "Planned Movie",
                    "image": "http://example.com/image.jpg",
                    "max_progress": 1,
                }

            return {"max_progress": None}

        self.mock_get_media_metadata.side_effect = mock_get_media_metadata

        season_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=season_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        base_watched_at = timezone.now()
        for i in range(1, 6):  # Create 5 episodes
            episode_item = Item.objects.create(
                media_id="1668",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title="Test TV Show",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=i,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=self.season,
                end_date=base_watched_at - timezone.timedelta(days=6 - i),
            )

        anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=10,
        )

        movie_item = Item.objects.create(
            media_id="10",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Planned Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

    def test_home_view(self):
        """Test the home view displays in-progress and planning media."""
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/home.html")

        self.assertIn("home_sections", response.context)

        sections_by_key = {
            section["key"]: section for section in response.context["home_sections"]
        }
        self.assertIn(Status.IN_PROGRESS.value, sections_by_key)
        self.assertIn(Status.PLANNING.value, sections_by_key)

        in_progress_section = sections_by_key[Status.IN_PROGRESS.value]
        planning_section = sections_by_key[Status.PLANNING.value]

        self.assertIn(MediaTypes.SEASON.value, in_progress_section["media_types"])
        self.assertIn(MediaTypes.ANIME.value, in_progress_section["media_types"])
        self.assertIn(MediaTypes.MOVIE.value, planning_section["media_types"])

        self.assertIn("sort_choices", response.context)
        self.assertEqual(response.context["sort_choices"], HomeSortChoices.choices)
        self.assertEqual(in_progress_section["count"], 2)
        self.assertEqual(planning_section["count"], 1)

        season = in_progress_section["media_types"][MediaTypes.SEASON.value]
        self.assertEqual(len(season["items"]), 1)
        self.assertEqual(season["items"][0].progress, 5)

        planning_movies = planning_section["media_types"][MediaTypes.MOVIE.value]
        self.assertEqual(len(planning_movies["items"]), 1)
        self.assertEqual(planning_movies["items"][0].status, Status.PLANNING.value)

    def test_home_uses_compact_swipe_list_by_default(self):
        """The default home layout renders swipe actions for in-progress items."""
        Movie.objects.update(status=Status.IN_PROGRESS.value)
        game_item = Item.objects.create(
            media_id="home-game",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Test Game",
            image="http://example.com/game.jpg",
        )
        game = Game(
            item=game_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=90,
        )
        Game.save_base(game)
        response = self.client.get(reverse("home"))

        self.assertContains(response, "data-home-list-item")
        self.assertContains(response, "homeSwipe({ enabled: true })")
        self.assertContains(
            response,
            'hx-vals=\'{"operation": "increase", "home_layout": "list", '
            '"home_section": "1"}\'',
        )
        self.assertContains(response, "Swipe either way or tap to mark progress")
        self.assertContains(response, 'aria-label="Mark watched"')
        self.assertContains(response, "Current progress · 1h 30min")
        self.assertContains(response, 'aria-label="Mark current episode unwatched"')
        self.assertContains(
            response,
            reverse(
                "episode_info",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": Anime.objects.get(user=self.user).id,
                },
            ),
        )
        self.assertContains(
            response,
            'hx-vals=\'{"operation": "decrease", "home_layout": "list", '
            '"home_section": "1"}\'',
        )
        self.assertContains(response, "-mx-[27px] space-y-4 sm:mx-0")

    def test_home_can_restore_card_grid(self):
        """The saved card setting keeps the previous homepage presentation."""
        self.user.home_layout = HomeLayoutChoices.GRID
        self.user.save(update_fields=["home_layout"])

        response = self.client.get(reverse("home"))

        self.assertNotContains(response, "data-home-list-item")
        self.assertContains(response, 'class="media-grid"')
        self.assertContains(response, 'title="Add to tracker"')
        self.assertContains(response, '"home_section": "1"')

    def test_home_list_does_not_invent_episode_after_provider_max(self):
        """A stuck finale row should not claim a nonexistent next episode."""
        finale_item = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test TV Show",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=25,
        )
        finale = Episode(
            item=finale_item,
            related_season=self.season,
            end_date=timezone.now(),
        )
        Episode.save_base(finale)
        Event.objects.create(
            item=self.season.item,
            content_number=25,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Season ready to finish")
        self.assertNotContains(response, "S01E26")

    def test_home_list_shows_upcoming_releases(self):
        """Backlog and in-progress rows show releases in list layout only."""
        planned_movie = Movie.objects.get(user=self.user)
        Event.objects.create(
            item=planned_movie.item,
            datetime=timezone.now() + timezone.timedelta(days=7),
        )
        in_progress_anime = Anime.objects.get(user=self.user)
        Event.objects.create(
            item=in_progress_anime.item,
            content_number=11,
            datetime=timezone.now() + timezone.timedelta(days=3),
        )

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Releases ", count=2)
        self.assertContains(response, "E11 •")

        self.user.home_layout = HomeLayoutChoices.GRID
        self.user.save(update_fields=["home_layout"])
        response = self.client.get(reverse("home"))

        self.assertNotContains(response, "Releases ")

    def test_episode_info_returns_the_specific_next_episode(self):
        """The home episode sheet describes the episode after current progress."""
        self.mock_get_media_metadata.side_effect = None
        self.mock_get_media_metadata.return_value = {
            "media_id": "1668",
            "source": Sources.TMDB.value,
            "season_number": 1,
            "episodes": [
                {
                    "episode_number": episode_number,
                    "air_date": "2026-07-02",
                    "still_path": f"/episode-{episode_number}.jpg",
                    "name": f"Episode title {episode_number}",
                    "overview": f"Episode synopsis {episode_number}",
                    "runtime": 54,
                }
                for episode_number in range(1, 11)
            ],
        }

        response = self.client.get(
            reverse(
                "episode_info",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": self.season.id,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "S01E06")
        self.assertContains(response, "Episode title 6")
        self.assertContains(response, "Episode synopsis 6")
        self.assertContains(response, "54m")
        self.assertContains(response, "Mark as watched")
        self.assertContains(response, '"home_section": "1"')
        self.assertNotContains(response, "Episode title 5")
        self.assertTrue(
            Item.objects.filter(
                media_id="1668",
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=6,
            ).exists(),
        )

    def test_episode_info_returns_next_anime_episode(self):
        """The home episode sheet supports episodic anime rows."""
        self.mock_get_media_metadata.side_effect = None
        self.mock_get_media_metadata.return_value = {
            "title": "Test Anime",
            "image": "http://example.com/anime.jpg",
            "max_progress": 24,
            "synopsis": "Anime synopsis",
            "details": {"runtime": "24 min"},
        }
        anime = Anime.objects.get(user=self.user)

        response = self.client.get(
            reverse(
                "episode_info",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": anime.id,
                },
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "E11")
        self.assertContains(response, "Test Anime")
        self.assertContains(response, "Anime synopsis")
        self.assertContains(response, "24 min")
        self.assertContains(response, "Mark as watched")
        self.assertContains(
            response,
            reverse(
                "progress_edit",
                kwargs={
                    "media_type": MediaTypes.ANIME.value,
                    "instance_id": anime.id,
                },
            ),
        )

    def test_episode_info_rejects_another_users_season(self):
        """Episode metadata cannot be requested for someone else's tracker row."""
        other_user = get_user_model().objects.create_user(
            username="other",
        )
        self.client.force_login(other_user)

        response = self.client.get(
            reverse(
                "episode_info",
                kwargs={
                    "media_type": MediaTypes.SEASON.value,
                    "instance_id": self.season.id,
                },
            ),
        )

        self.assertEqual(response.status_code, 404)

    def test_home_view_with_sort(self):
        """Test the home view with sorting parameter."""
        response = self.client.get(reverse("home") + "?sort=completion")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "completion")

        self.user.refresh_from_db()
        self.assertEqual(self.user.home_sort, "completion")

    def test_home_aired_only_toggle_persists(self):
        """The homepage control saves and reflects the aired-only preference."""
        response = self.client.get(reverse("home") + "?aired_only=1")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.home_aired_only)
        self.assertContains(response, "Aired only")
        self.assertContains(response, 'aria-pressed="true"')

        response = self.client.get(reverse("home") + "?aired_only=0")

        self.user.refresh_from_db()
        self.assertFalse(self.user.home_aired_only)
        self.assertContains(response, 'aria-pressed="false"')

    @patch("app.providers.services.get_media_metadata")
    def test_home_view_htmx_load_more(self, mock_get_media_metadata):
        """Test the HTMX load more functionality."""
        mock_get_media_metadata.return_value = {
            "title": "Test TV Show",
            "image": "http://example.com/image.jpg",
            "season/1": {
                "episodes": [{"id": 1}, {"id": 2}, {"id": 3}],  # 3 episodes
            },
            "related": {
                "seasons": [
                    {"season_number": 1, "image": "http://example.com/image.jpg"},
                ],  # Only one season
            },
        }

        for i in range(6, 20):  # Create 14 more TV shows (we already have 1)
            season_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
                season_number=1,
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                status=Status.IN_PROGRESS.value,
            )

            episode_item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Test TV Show {i}",
                image="http://example.com/image.jpg",
                season_number=1,
                episode_number=1,
            )
            Episode.objects.create(
                item=episode_item,
                related_season=season,
                end_date=timezone.now(),
            )

        # Now test the load more functionality
        response = self.client.get(
            reverse("home") + "?load_media_type=season", headers={"hx-request": "true"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_grid.html")

        self.assertIn("media_list", response.context)
        self.assertEqual(response.context["home_status"], Status.IN_PROGRESS.value)

        self.assertIn("items", response.context["media_list"])
        self.assertIn("total", response.context["media_list"])

        # Since we're loading more (items after the first 14),
        # we should have at least 1 item in the response
        self.assertEqual(len(response.context["media_list"]["items"]), 1)
        self.assertEqual(
            response.context["media_list"]["total"],
            15,
        )  # 15 TV shows total

    def test_home_view_htmx_load_more_for_planning(self):
        """Test the HTMX load more functionality for planning media."""
        self.user.show_all_home_items = True
        self.user.save(update_fields=["show_all_home_items"])

        for i in range(1, 16):
            movie_item = Item.objects.create(
                media_id=f"planning-{i}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Planned Movie {i}",
                image="http://example.com/image.jpg",
            )
            Movie.objects.create(
                item=movie_item,
                user=self.user,
                status=Status.PLANNING.value,
            )

        home_response = self.client.get(reverse("home"))

        planning_section = next(
            section
            for section in home_response.context["home_sections"]
            if section["key"] == Status.PLANNING.value
        )
        self.assertEqual(
            len(planning_section["media_types"][MediaTypes.MOVIE.value]["items"]),
            16,
        )
        self.assertNotContains(home_response, "Load all (16)")

        self.user.show_all_home_items = False
        self.user.save(update_fields=["show_all_home_items"])
        home_response = self.client.get(reverse("home"))

        self.assertContains(home_response, "Load all (16)")
        self.assertContains(home_response, "hx-on::after-request=")
        self.assertNotContains(home_response, 'hx-on:click="this.remove()"')

        response = self.client.get(
            reverse("home")
            + (
                f"?load_status={Status.PLANNING.value}"
                f"&load_media_type={MediaTypes.MOVIE.value}"
            ),
            headers={"hx-request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/home_grid.html")
        self.assertEqual(response.context["home_status"], Status.PLANNING.value)
        self.assertIn("media_list", response.context)
        self.assertEqual(len(response.context["media_list"]["items"]), 2)
        self.assertEqual(response.context["media_list"]["total"], 16)


class HomeMediaTypeOrderTests(TestCase):
    """Test that the home media-type rows honor the user's saved order."""

    def setUp(self):
        """Create a user (all media types enabled by default)."""
        credentials = {"username": "ordertest", "password": "12345"}
        self.user = get_user_model().objects.create_user(**credentials)

    def test_default_order_is_enum_order(self):
        """With no saved order, TV is excluded and season leads the enum order."""
        result = BasicMedia.objects._get_media_types_to_process(self.user, None)
        self.assertNotIn(MediaTypes.TV.value, result)
        self.assertEqual(result[0], MediaTypes.SEASON.value)

    def test_saved_order_moves_type_to_front(self):
        """A saved order puts the chosen type first, others keep enum order."""
        self.user.home_media_order = MediaTypes.GAME.value
        self.user.save()
        result = BasicMedia.objects._get_media_types_to_process(self.user, None)
        self.assertEqual(result[0], MediaTypes.GAME.value)
        self.assertNotIn(MediaTypes.TV.value, result)
