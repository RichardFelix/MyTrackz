import os
from datetime import date
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.providers import services
from app.tests.test_sneakers import SHOE
from users.models import HomeLayoutChoices


class IntegrationTest(StaticLiveServerTestCase):
    """Integration tests for the application."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        # use headless=False, slow_mo=200 to see the browser
        cls.browser = cls.playwright.chromium.launch()
        cls.page = cls.browser.new_page()

    def setUp(self):
        """Set up test data for CustomList model."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.page.goto(f"{self.live_server_url}/")
        self.page.get_by_placeholder("Enter your username").fill(
            self.credentials["username"],
        )
        self.page.get_by_placeholder("Enter your password").fill(
            self.credentials["password"],
        )
        self.page.get_by_role("button", name="Sign in").click()

    @classmethod
    def tearDownClass(cls):
        """Tear down the test class."""
        super().tearDownClass()
        cls.browser.close()
        cls.playwright.stop()

    def palette_search(self, query, media_type_display):
        """Search via the command palette that replaced the top-bar search form."""
        self.page.get_by_role("button", name="Search or jump to").click()
        palette_input = self.page.locator("#palette-input")
        expect(palette_input).to_be_visible()
        self.page.get_by_role("button", name=media_type_display, exact=True).click()
        palette_input.fill(query)
        palette_input.press("Enter")

    def test_planning_status_is_displayed_as_wishlist(self):
        """Status navigation and forms default to Wishlist but retain Planning."""
        expect(
            self.page.get_by_role("link", name="Wishlist", exact=True)
        ).to_be_visible()

        self.page.get_by_role("link", name="Create Custom", exact=True).click()
        planning_option = self.page.locator(
            'select[name="status"] option[value="Planning"]'
        )
        expect(planning_option).to_have_text("Wishlist")

    @patch("app.providers.sneakers._request")
    def test_sneaker_collection(self, request):
        """Search, record MSRP, and edit saved shoes offline on mobile."""
        request.side_effect = lambda path, *_args, **_kwargs: (
            {"data": [SHOE], "meta": None} if path == "goat/products" else {"data": []}
        )
        # The adapter checks configuration before using the mocked HTTP client.
        with override_settings(KICKSDB_API="test-key"):
            self.palette_search("DD1391-100", "Sneakers")
            self.page.get_by_title(SHOE["name"], exact=True).click()
            expect(self.page.get_by_role("main")).to_contain_text("DD1391 100")
            self.page.get_by_role("button", name="Add to tracker").click()
            self.page.get_by_label("Owned pairs").fill("1")
            self.page.get_by_label("Original MSRP", exact=True).fill("100")
            self.page.get_by_label("MSRP currency").fill("USD")
            self.page.get_by_label("Status").select_option("Completed")
            self.page.get_by_role("button", name="Add", exact=True).click()
            expect(self.page.get_by_role("main")).to_contain_text("USD 100.00")
        request.reset_mock()
        request.side_effect = AssertionError("Saved sneakers must work offline")
        self.page.set_viewport_size({"width": 390, "height": 844})
        try:
            self.page.reload()
            self.page.get_by_role("button", name="Edit collection").click()
            expect(self.page.get_by_label("Original MSRP", exact=True)).to_have_value(
                "100.00"
            )
            self.page.get_by_label("Owned pairs").fill("2")
            self.page.get_by_role("button", name="Update", exact=True).click()
            expect(self.page.get_by_role("main")).to_contain_text("Owned pairs: 2")
            self.assertLessEqual(
                self.page.evaluate("document.documentElement.scrollWidth"), 390
            )
        finally:
            self.page.set_viewport_size({"width": 1280, "height": 720})
        request.assert_not_called()

    @override_settings(KICKSDB_API="test-key")
    @patch("app.providers.sneakers._request")
    def test_sneaker_page_two_outage_and_retry(self, request):
        """An upstream outage keeps navigation usable and Retry loads page two."""
        cache.clear()
        second_page_calls = 0

        def catalog_response(_path, params):
            nonlocal second_page_calls
            if params["page"] == 2:  # noqa: PLR2004
                second_page_calls += 1
                if second_page_calls <= 2:  # noqa: PLR2004
                    response = requests.Response()
                    response.status_code = 500
                    raise services.ProviderAPIError(
                        Sources.KICKSDB, requests.HTTPError(response=response)
                    )
                shoe = {**SHOE, "id": 2, "name": "Air Jordan 3 Second Page"}
            else:
                shoe = {**SHOE, "id": 1, "name": "Air Jordan 3 First Page"}
            return {"data": [shoe], "meta": {"total": 40}}

        request.side_effect = catalog_response
        self.palette_search("Air Jordan 3", "Sneakers")
        expect(
            self.page.get_by_title("Air Jordan 3 First Page", exact=True)
        ).to_be_visible()
        self.page.locator('a[href*="page=2"]').first.click()
        expect(self.page.get_by_role("alert")).to_contain_text(
            "Page 2 could not be loaded"
        )
        expect(self.page.get_by_role("link", name="Back to page 1")).to_be_visible()
        expect(self.page.get_by_text("No results found", exact=True)).to_have_count(0)
        self.page.get_by_role("link", name="Try again", exact=True).click()
        expect(
            self.page.get_by_title("Air Jordan 3 Second Page", exact=True)
        ).to_be_visible()
        self.assertEqual(second_page_calls, 3)

    @patch("app.providers.rebrickable._request")
    def test_lego_build_tracking(self, request):
        """Search, track and display build time through the real browser UI."""
        catalog_set = {
            "set_num": "75192-1",
            "name": "Millennium Falcon",
            "year": 2017,
            "num_parts": 7541,
            "set_img_url": None,
        }
        request.side_effect = lambda _path, params=None: (
            {"count": 1, "results": [catalog_set]} if params else catalog_set
        )
        self.palette_search("75192", "Lego")
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Millennium Falcon", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("7541")
        expect(self.page.get_by_role("main")).to_contain_text("75192-1")
        self.page.get_by_role("button", name="Add to tracker").click()
        expect(self.page.locator("#track-lego-75192-1")).to_contain_text("Build time")
        self.page.get_by_label("Build time (hours:minutes)").fill("1:30")
        self.page.get_by_label("Status").select_option("In progress")
        self.page.get_by_role("button", name="Add", exact=True).click()
        self.page.get_by_role("link", name="Lego", exact=True).click()
        self.page.get_by_role("link", name="Table View").click()
        expect(self.page.locator("tbody")).to_contain_text("1h 30min")
        expect(self.page.locator("tbody")).to_contain_text("In Progress")
        self.page.get_by_role("link", name="Home", exact=True).click()
        self.page.get_by_role("button", name="Increase progress", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("2h 00min")
        self.page.set_viewport_size({"width": 390, "height": 844})
        try:
            self.page.reload()
            expect(self.page.get_by_role("main")).to_contain_text("Lego")
            expect(self.page.get_by_role("main")).to_contain_text("2h 00min")
            self.assertLessEqual(
                self.page.evaluate("document.documentElement.scrollWidth"),
                390,
            )
        finally:
            self.page.set_viewport_size({"width": 1280, "height": 720})

    def test_season_progress_edit(self):
        """Test the progress edit of a season."""
        self.user.home_layout = HomeLayoutChoices.LIST
        self.user.save(update_fields=["home_layout"])

        self.palette_search("breaking bad", "TV Shows")
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.get_by_title("Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.locator(".p-2").first.click()
        expect(self.page.get_by_role("main")).to_contain_text("Track Episode")
        self.page.locator(".relative > .px-2").first.click()
        self.page.get_by_role("button", name="Fill in air date").click()
        self.page.get_by_role("button", name="Add watch").click()

        datetime_format = "%Y-%m-%d"

        # Episode 1 air date is 2008-01-20
        fixed_date = date(2008, 1, 20)

        expect(self.page.get_by_role("main")).to_contain_text(
            f"Last watched: {fixed_date.strftime(datetime_format)}",
        )
        self.page.get_by_role("link", name="Home").click()
        expect(self.page.get_by_title("Breaking Bad S1")).to_be_visible()
        swipe_target = self.page.get_by_text("S01E02 · Next episode")
        box = swipe_target.bounding_box()
        self.page.mouse.move(box["x"] + 10, box["y"] + 10)
        self.page.mouse.down()
        self.page.mouse.move(box["x"] + 100, box["y"] + 10, steps=4)
        self.page.mouse.up()
        expect(self.page.get_by_text("S01E03 · Next episode")).to_be_visible()
        self.page.get_by_title("Breaking Bad S1").click()

        today = timezone.localtime().strftime(datetime_format)
        expect(self.page.get_by_role("main")).to_contain_text(f"Last watched: {today}")

    def test_tv_completed(self):
        """Test the completed status of a TV show."""
        self.palette_search("breaking bad", "TV Shows")
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.locator("button").filter(has_text="Add to tracker").click()
        expect(self.page.locator("#track-tv-1396")).to_contain_text("Score")
        self.page.get_by_label("Status").select_option("Completed")
        self.page.get_by_role("button", name="Add", exact=True).click()
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Table View").click()
        expect(self.page.locator("tbody")).to_contain_text("62")

    def test_season_completed(self):
        """Test the completed status of a season."""
        self.palette_search("breaking bad", "TV Shows")
        expect(self.page.locator("h2")).to_contain_text("Search Results")
        self.page.get_by_title("Breaking Bad", exact=True).click()
        expect(self.page.get_by_role("main")).to_contain_text("Breaking Bad")
        self.page.get_by_title("Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.get_by_role("button", name="Add to tracker").click()
        expect(self.page.locator("#track-season-1396-1")).to_contain_text("Score")
        self.page.get_by_label("Status").select_option("Completed")
        self.page.get_by_role("button", name="Add", exact=True).click()
        self.page.get_by_role("link", name="TV Seasons").click()
        self.page.get_by_role("link", name="Table View").click()
        expect(self.page.locator("tbody")).to_contain_text("Completed")
        expect(self.page.locator("tbody")).to_contain_text("7")

    def test_tv_manual(self):
        """Test the manual creation of a TV show."""
        # Create TV show
        self.page.get_by_role("link", name="Create Custom", exact=True).click()
        self.page.get_by_placeholder("Enter title").click()
        self.page.get_by_placeholder("Enter title").fill("Friends")
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w300_and_h450_bestv2/2koX1xLkpTQM4IZebYvKysFW1Nh.jpg",
        )
        self.page.get_by_role("combobox").select_option("In progress")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator(".scheme-dark")).to_contain_text(
            "Friends added successfully.",
        )

        # Create season
        self.page.get_by_role("button", name="Season").click()
        expect(self.page.get_by_role("main")).to_contain_text("Parent TV Show")
        self.page.get_by_placeholder("Search for a TV show...").click()
        self.page.get_by_placeholder("Search for a TV show...").type("fri")
        expect(self.page.locator("#parent-tv-results")).to_contain_text("Friends")
        self.page.get_by_role("button", name="Friends").click()
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w130_and_h195_bestv2/odCW88Cq5hAF0ZFVOkeJmeQv1nV.jpg",
        )
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator("body")).to_contain_text(
            "Friends S1 added successfully.",
        )

        # Create episode
        self.page.get_by_role("button", name="Episode").click()
        expect(self.page.get_by_role("main")).to_contain_text("Parent Season")
        self.page.get_by_placeholder("Search for a season...").click()
        self.page.get_by_placeholder("Search for a season...").type("frien")
        expect(self.page.locator("#parent-season-results")).to_contain_text(
            "Friends - Season 1",
        )
        self.page.get_by_role("button", name="Friends - Season").click()
        self.page.get_by_placeholder("Enter image URL").click()
        self.page.get_by_placeholder("Enter image URL").fill(
            "https://media.themoviedb.org/t/p/w227_and_h127_bestv2/v6Elr1W2elOyGi1MClgV0mIBVHC.jpg",
        )
        self.page.locator('input[name="end_date"]').fill("2025-03-07")
        self.page.get_by_role("button", name="Create Entry").click()
        expect(self.page.locator("body")).to_contain_text(
            "Friends S1E1 added successfully.",
        )

        # Check visibility
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_role("link", name="Grid View").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        self.page.get_by_role("link", name="TV Seasons").click()
        self.page.get_by_role("link", name="Grid View").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends S1")
        self.page.get_by_role("link", name="TV Shows").click()
        self.page.get_by_title("Friends").click()
        expect(self.page.get_by_role("main")).to_contain_text("Friends")
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        self.page.get_by_title("Season 1").click()
        expect(self.page.get_by_role("main")).to_contain_text("Season 1")
        expect(self.page.get_by_role("main")).to_contain_text(
            "Episode 1 • Unknown air date",
        )

    def test_obfuscate_unseen_episodes_enabled(self):
        """Test that obfuscate_unseen_episodes setting is accessible and functional."""
        # Navigate to preferences
        self.page.get_by_role("link", name="Settings").click()
        self.page.get_by_role("link", name="Preferences").click()

        # Verify the obfuscate setting is visible
        expect(self.page.get_by_role("main")).to_contain_text(
            "Obfuscate unseen episodes"
        )
        expect(self.page.get_by_role("main")).to_contain_text(
            "Blur images and descriptions for episodes you have not watched."
        )

        # Find and check the obfuscate checkbox by clicking the label
        # The checkbox is sr-only (hidden), so we need to click the label
        obfuscate_label = self.page.locator(
            'label:has(input[name="obfuscate_unseen_episodes"])'
        )
        obfuscate_checkbox = self.page.locator(
            'input[name="obfuscate_unseen_episodes"]'
        )
        if not obfuscate_checkbox.is_checked():
            obfuscate_label.click()

        # Save preferences
        self.page.get_by_role("button", name="Save").nth(1).click()

        # Verify success message
        expect(self.page.locator(".scheme-dark")).to_contain_text("Settings updated")

        # Verify setting persisted
        self.page.get_by_role("link", name="Preferences").click()
        obfuscate_checkbox = self.page.locator(
            'input[name="obfuscate_unseen_episodes"]'
        )
        expect(obfuscate_checkbox).to_be_checked()

    def test_obfuscate_unseen_episodes_disabled(self):
        """Test toggling obfuscate_unseen_episodes setting off."""
        # Navigate to preferences
        self.page.get_by_role("link", name="Settings").click()
        self.page.get_by_role("link", name="Preferences").click()

        # Find the obfuscate checkbox
        obfuscate_checkbox = self.page.locator(
            'input[name="obfuscate_unseen_episodes"]'
        )
        if obfuscate_checkbox.is_checked():
            # Click the label to uncheck (checkbox is sr-only, so click label)
            obfuscate_label = self.page.locator(
                'label:has(input[name="obfuscate_unseen_episodes"])'
            )
            obfuscate_label.click()

            # Save preferences
            self.page.get_by_role("button", name="Save").nth(1).click()

            # Verify success message
            expect(self.page.locator(".scheme-dark")).to_contain_text(
                "Settings updated"
            )

            # Verify setting persisted as unchecked
            self.page.get_by_role("link", name="Preferences").click()
            obfuscate_checkbox = self.page.locator(
                'input[name="obfuscate_unseen_episodes"]'
            )
            expect(obfuscate_checkbox).not_to_be_checked()

    def _create_library_movies(self, count):
        """Track `count` planned movies titled "Movie 00".. for library tests."""
        movies = []
        for i in range(count):
            item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Movie {i:02d}",
                image="http://example.com/image.jpg",
            )
            movies.append(
                Movie.objects.create(
                    item=item,
                    user=self.user,
                    status=Status.PLANNING.value,
                ),
            )
        return movies

    def _open_library(self, layout):
        self.page.goto(
            f"{self.live_server_url}/{self.user.username}/movie"
            f"?layout={layout}&sort=title&status=All",
        )
        # survives only while the page is never reloaded
        self.page.evaluate("window.__libraryNoReload = true")

    def _edit_card(self, movie):
        card = self.page.locator(f"#library-item-{movie.item_id}")
        card.hover()
        card.locator("[hx-get*='track_modal']").click()
        return card

    @patch("app.providers.services.get_media_metadata")
    def test_library_edit_updates_card_in_place(self, metadata):
        """Editing from the library grid swaps the card without a reload."""
        metadata.return_value = {"max_progress": 1}
        movies = self._create_library_movies(3)
        self._open_library("grid")

        card = self._edit_card(movies[1])
        self.page.get_by_label("Score").fill("8")
        self.page.get_by_role("button", name="Update").click()

        expect(card).to_contain_text("8")
        expect(self.page.locator(".modal-glass:visible")).to_have_count(0)
        self.assertTrue(self.page.evaluate("window.__libraryNoReload === true"))

        # the swapped card can be edited again
        card = self._edit_card(movies[1])
        self.page.get_by_role("button", name="Delete").click()
        expect(card).to_have_count(0)
        expect(self.page.locator("#media-item-count")).to_contain_text("2 items")
        self.assertTrue(self.page.evaluate("window.__libraryNoReload === true"))

    @patch("app.providers.services.get_media_metadata")
    def test_library_table_edit_closes_modal(self, metadata):
        """Table rows keep their Alpine state, so the modal must close itself."""
        metadata.return_value = {"max_progress": 1}
        movies = self._create_library_movies(2)
        self._open_library("table")

        row = self._edit_card(movies[0])
        self.page.get_by_label("Score").fill("6")
        self.page.get_by_role("button", name="Update").click()

        expect(row.locator("td").nth(2)).to_contain_text("6")
        expect(self.page.locator(".modal-glass:visible")).to_have_count(0)
        self.assertTrue(self.page.evaluate("window.__libraryNoReload === true"))

    @patch("app.providers.services.get_media_metadata")
    def test_library_delete_keeps_infinite_scroll_complete(self, metadata):
        """Removing the next-page trigger card still loads every later item."""
        metadata.return_value = {"max_progress": 1}
        movies = self._create_library_movies(40)
        self._open_library("grid")
        cards = self.page.locator("[data-library-item]")
        expect(cards).to_have_count(32)

        # Movie 31 is the last card of page 1 and carries the page-2 trigger
        card = self._edit_card(movies[31])
        self.page.get_by_role("button", name="Delete").click()
        expect(card).to_have_count(0)
        # and a mid-page delete shifts page 2 back once more
        card = self._edit_card(movies[5])
        self.page.get_by_role("button", name="Delete").click()
        expect(card).to_have_count(0)

        self.page.mouse.wheel(0, 100000)
        expect(cards).to_have_count(38)
        titles = cards.locator("a[title]").all_inner_texts()
        expected = [f"Movie {i:02d}" for i in range(40) if i not in (5, 31)]
        self.assertEqual(titles, expected)
        self.assertTrue(self.page.evaluate("window.__libraryNoReload === true"))
