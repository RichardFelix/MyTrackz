"""Exercise catalog import, offline collection editing and CSV persistence."""

import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse

from app.forms import SneakerForm
from app.models import Item, MediaTypes, Sneaker, SneakerProduct, Sources, Status
from app.providers import services, sneakers
from events.models import Event
from integrations.exports import generate_rows
from integrations.imports import yamtrack

SHOE = {
    "id": 719082,
    "name": "Dunk Low 'Black White'",
    "sku": "DD1391 100",
    "brand": "Nike",
    "colorway": "White/Black",
    "release_date": "2021-03-10T23:59:59Z",
    "image_url": None,
    "slug": "dunk-low-black-white-dd1391-100",
    "product_type": "sneakers",
}


@override_settings(KICKSDB_API="test-key")
class SneakerCatalogTests(TestCase):
    """Free-tier missing fields must not break a permanent local snapshot."""

    def setUp(self):
        """Keep search caches independent across tests."""
        cache.clear()

    @patch("app.providers.services.api_request")
    def test_search_capture_and_offline_read(self, request):
        """The first selection captures data; later reads use the database."""
        request.side_effect = [
            {"data": [SHOE], "meta": None},
            {"data": []},
        ]
        results = services.search("sneaker", "DD1391-100", 1)
        self.assertEqual(results["results"][0]["media_id"], "goat-719082")
        self.assertEqual(results["total_pages"], 1)
        self.assertEqual(
            request.call_args.kwargs["headers"],
            {"Authorization": "Bearer test-key"},
        )
        self.assertNotIn("key", request.call_args.kwargs["params"])
        metadata = services.get_media_metadata("sneaker", "goat-719082", "kicksdb")
        product = metadata["catalog_product"]
        self.assertEqual(product.release_date, date(2021, 3, 10))
        self.assertEqual(product.style_code, "DD1391 100")
        self.assertIsNone(product.reported_retail_price)
        self.assertIsNone(metadata["max_progress"])
        cache.clear()
        request.reset_mock()
        request.side_effect = requests.ConnectionError()
        with override_settings(KICKSDB_API=""):
            self.assertEqual(sneakers.sneaker(product.pk), metadata)
            local = sneakers.search("DD1391", 1)
            self.assertEqual(local["results"][0]["media_id"], product.pk)
        request.assert_not_called()

    @patch("app.providers.services.api_request")
    def test_retail_claim_is_optional_and_separate(self, request):
        """Even a returned retail figure is never assigned as original MSRP."""
        stockx_id = "cd4cf1f9-48dd-4c0a-9c88-9642286642bd"
        request.side_effect = [
            {"data": SHOE},
            {"data": [{"id": stockx_id, "sku": "DD1391-100"}]},
            {"data": {"traits": [{"trait": "Retail Price", "value": 115}]}},
        ]
        product = sneakers.sneaker("goat-719082")["catalog_product"]
        self.assertEqual(product.reported_retail_price, Decimal("115.00"))
        user = get_user_model().objects.create_user(username="collector")
        item = Item.objects.create(
            media_id=product.pk,
            source=Sources.KICKSDB,
            media_type=MediaTypes.SNEAKER,
            title=product.name,
        )
        entry = Sneaker.objects.create(item=item, user=user)
        self.assertIsNone(entry.original_msrp)
        self.assertEqual(entry.msrp_currency, "")

    @patch("app.providers.services.api_request")
    def test_enrichment_failure_keeps_shoe_and_rejects_sentinel_date(self, request):
        """A failed StockX request still produces a usable GOAT catalog entry."""
        request.side_effect = [
            {"data": {**SHOE, "release_date": "0001-01-01T00:00:00Z"}},
            requests.ConnectionError(),
        ]
        product = sneakers.sneaker("goat-719082")["catalog_product"]
        self.assertIsNone(product.release_date)
        self.assertIsNone(product.reported_retail_price)
        self.assertEqual(product.name, SHOE["name"])

    def test_bad_retail_claims_and_executable_images_are_ignored(self):
        """Nulls, sentinels and invalid URLs remain unknown metadata."""
        for value in [None, "", "NaN", "Infinity", -1, "not a number"]:
            with self.subTest(value=value):
                self.assertIsNone(sneakers._retail_price({"Retail Price": value}))
        self.assertEqual(sneakers._url("javascript:alert(1)"), "")

    @patch("app.providers.services.session.get")
    def test_exhausted_quota_does_not_retry_forever(self, request):
        """A monthly quota failure returns promptly instead of blocking the UI."""
        response = requests.Response()
        response.status_code = 429
        request.return_value = response
        with self.assertRaises(services.ProviderAPIError):
            sneakers._request("goat/products")
        request.assert_called_once()

    @patch("app.providers.services.api_request")
    def test_search_pages_are_distinct_and_cached(self, request):
        """Page two requests the correct page and preserves its own cache."""
        first = [{**SHOE, "id": number} for number in range(1, 21)]
        second = [{**SHOE, "id": number} for number in range(21, 41)]
        request.side_effect = [
            {"data": first, "meta": {"total": 40}},
            {"data": second, "meta": {"total": 40}},
        ]
        page_one = sneakers.search("Air Jordan 3", 1)
        page_two = sneakers.search("Air Jordan 3", 2)
        self.assertEqual(page_two["page"], 2)
        self.assertEqual(page_two["total_pages"], 2)
        self.assertEqual(page_two["results"][0]["media_id"], "goat-21")
        self.assertNotEqual(page_one["results"], page_two["results"])
        self.assertEqual(request.call_args.kwargs["params"]["page"], 2)
        self.assertEqual(sneakers.search("Air Jordan 3", 2), page_two)
        self.assertEqual(request.call_count, 2)

    @patch("app.providers.services.api_request")
    def test_page_two_retries_a_transient_server_error(self, request):
        """A temporary KicksDB 500 gets exactly one retry on the same page."""
        response = requests.Response()
        response.status_code = 500
        request.side_effect = [
            requests.HTTPError(response=response),
            {"data": [SHOE], "meta": {"total": 40}},
        ]
        result = sneakers.search("Air Jordan 3", 2)
        self.assertEqual(result["results"][0]["media_id"], "goat-719082")
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertIn("goat/products", call.args[2])
            self.assertEqual(call.kwargs["params"]["page"], 2)

    @patch("app.providers.services.api_request")
    def test_page_two_outage_renders_search_and_recovers(self, request):
        """Outages preserve navigation and aren't cached as empty matches."""
        response = requests.Response()
        response.status_code = 500
        request.side_effect = requests.HTTPError(response=response)
        user = get_user_model().objects.create_user(username="pagination")
        self.client.force_login(user)
        url = reverse("search") + "?q=air+jordan+3&media_type=sneaker&page=2"
        result = self.client.get(url)
        self.assertContains(result, "Page 2 could not be loaded")
        self.assertContains(result, "Try again")
        self.assertContains(result, "Back to page 1")
        self.assertNotContains(result, "No results found")
        self.assertNotContains(result, "{%")
        self.assertEqual(request.call_count, 2)
        request.side_effect = None
        request.return_value = {"data": [SHOE], "meta": {"total": 40}}
        result = self.client.get(url)
        self.assertNotContains(result, "could not be loaded")
        self.assertContains(result, "Dunk Low")
        self.assertEqual(request.call_count, 3)

    @patch("app.providers.services.api_request")
    def test_stockx_fallback_is_preserved_on_page_two(self, request):
        """Paging must not switch marketplaces and skip or repeat shoes."""
        stockx = {**SHOE, "id": "cd4cf1f9-48dd-4c0a-9c88-9642286642bd"}
        request.side_effect = [
            requests.ConnectionError(),
            {"data": [stockx], "meta": {"total": 40}},
            {"data": [stockx], "meta": {"total": 40}},
        ]
        sneakers.search("Air Jordan 3", 1)
        sneakers.search("Air Jordan 3", 2)
        self.assertIn("stockx/products", request.call_args.args[2])
        self.assertEqual(request.call_args.kwargs["params"]["page"], 2)

    @patch("app.providers.services.api_request")
    def test_outage_saved_matches_are_explicitly_labeled(self, request):
        """A saved match is an offline fallback, not a fresh marketplace result."""
        SneakerProduct.objects.create(catalog_id="goat-3", name="Air Jordan 3")
        request.side_effect = requests.ConnectionError()
        user = get_user_model().objects.create_user(username="offline-search")
        self.client.force_login(user)
        response = self.client.get(
            reverse("search"),
            {
                "q": "Air Jordan 3",
                "media_type": "sneaker",
            },
        )
        self.assertContains(response, "Only previously saved matches")
        self.assertContains(response, "Air Jordan 3")

    @override_settings(KICKSDB_API="")
    @patch("app.providers.services.api_request")
    def test_missing_key_and_exact_page_boundary(self, request):
        """Missing keys are actionable; offline browsing has no phantom page."""
        with self.assertRaisesMessage(services.ProviderAPIError, "KICKSDB_API"):
            sneakers.search("Nike", 1)
        SneakerProduct.objects.bulk_create(
            [
                SneakerProduct(catalog_id=f"goat-{number}", name=f"Nike {number}")
                for number in range(20)
            ]
        )
        self.assertEqual(sneakers.search("Nike", 1)["total_pages"], 1)
        request.assert_not_called()


class SneakerCollectionTests(TestCase):
    """Personal overrides, history and imports must work without API access."""

    def setUp(self):
        """Create two collectors sharing a permanent catalog identity."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="collector")
        self.other = get_user_model().objects.create_user(username="other")
        self.client.force_login(self.user)
        self.product = SneakerProduct.objects.create(
            catalog_id="goat-719082",
            name=SHOE["name"],
            brand=SHOE["brand"],
            style_code=SHOE["sku"],
            colorway=SHOE["colorway"],
            source_url="https://www.goat.com/sneakers/dunk-low-black-white-dd1391-100",
            release_date=date(2021, 3, 10),
        )
        self.item = Item.objects.create(
            media_id=self.product.pk,
            source=Sources.KICKSDB,
            media_type=MediaTypes.SNEAKER,
            title=self.product.name,
        )
        self.data = {
            "media_id": self.product.pk,
            "source": Sources.KICKSDB,
            "media_type": MediaTypes.SNEAKER,
            "status": Status.COMPLETED,
            "progress": 2,
            "brand": "Nike",
            "style_code": SHOE["sku"],
            "colorway": SHOE["colorway"],
            "original_msrp": "100.00",
            "msrp_currency": "usd",
            "notes": "First pair",
        }

    @patch("app.providers.services.api_request")
    def test_save_edit_history_and_owner_isolation(self, request):
        """Editing MSRP or pair count neither refreshes prices nor changes others."""
        self.client.post(reverse("media_save"), self.data)
        entry = Sneaker.objects.get(user=self.user)
        self.assertEqual(entry.catalog_product, self.product)
        self.assertEqual(entry.original_msrp, Decimal("100.00"))
        self.assertEqual(entry.msrp_currency, "USD")
        self.assertEqual(entry.progress, 2)
        other_entry = Sneaker.objects.create(item=self.item, user=self.other)
        response = self.client.post(
            reverse("media_save"),
            {**self.data, "instance_id": other_entry.pk},
        )
        self.assertEqual(response.status_code, 404)
        self.client.post(
            reverse("media_save"),
            {**self.data, "instance_id": entry.pk, "progress": 3, "brand": "Mine"},
        )
        entry.refresh_from_db()
        other_entry.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(entry.progress, 3)
        self.assertEqual(entry.history.count(), 2)
        self.assertEqual(other_entry.brand, "Nike")
        self.assertEqual(self.product.brand, "Nike")
        self.assertFalse(Event.objects.filter(item=self.item).exists())
        request.assert_not_called()
        with self.assertRaises(ProtectedError):
            self.product.delete()

    def test_currency_and_price_validation(self):
        """Zero also requires currency, and negative MSRP is invalid."""
        for amount, currency in [("0", ""), ("-1", "USD"), ("100", "U$D")]:
            with self.subTest(amount=amount, currency=currency):
                form = SneakerForm(
                    {**self.data, "original_msrp": amount, "msrp_currency": currency},
                    instance=Sneaker(item=self.item, user=self.user),
                    user=self.user,
                )
                self.assertFalse(form.is_valid())

    @patch("app.providers.services.api_request")
    def test_details_and_tracking_form_work_without_key(self, request):
        """Saved pages and default metadata fields render during an API outage."""
        with override_settings(KICKSDB_API=""):
            response = self.client.get(
                reverse(
                    "media_details",
                    kwargs={
                        "source": "kicksdb",
                        "media_type": "sneaker",
                        "media_id": self.product.pk,
                        "title": "dunk",
                    },
                )
            )
            self.assertContains(response, "Your sneaker catalog")
            response = self.client.get(
                reverse(
                    "track_modal",
                    kwargs={
                        "source": "kicksdb",
                        "media_type": "sneaker",
                        "media_id": self.product.pk,
                    },
                ),
                {"return_url": "/"},
            )
            self.assertContains(response, "Owned pairs")
            self.assertContains(response, 'value="DD1391 100"')
            self.assertNotContains(response, "{%")
        request.assert_not_called()

    @patch("app.providers.services.api_request")
    def test_manual_creation_and_csv_roundtrip(self, request):
        """Manual catalog entries and personal metadata survive export/import."""
        self.client.post(
            reverse("create_entry"),
            {
                **self.data,
                "title": "Custom sneaker",
                "colorway": "Custom colors",
                "release_date": "2021-03-10",
            },
        )
        entry = Sneaker.objects.get(user=self.user)
        self.assertEqual(entry.item.source, Sources.MANUAL)
        self.assertEqual(entry.style_code, SHOE["sku"])
        exported = "".join(generate_rows(self.user)).encode()
        counts, warnings = yamtrack.importer(io.BytesIO(exported), self.other, "new")
        self.assertEqual(warnings, "")
        self.assertEqual(counts, {"sneaker": 1})
        imported = Sneaker.objects.get(user=self.other)
        for field in [
            "brand",
            "style_code",
            "colorway",
            "release_date",
            "original_msrp",
            "msrp_currency",
            "progress",
            "notes",
        ]:
            self.assertEqual(getattr(imported, field), getattr(entry, field))
        request.assert_not_called()

    def test_catalog_csv_import_keeps_snapshot_protected(self):
        """Bulk imports retain snapshot references despite bypassing model saves."""
        Sneaker.objects.create(item=self.item, user=self.user, progress=1)
        exported = "".join(generate_rows(self.user)).encode()
        counts, warnings = yamtrack.importer(io.BytesIO(exported), self.other, "new")
        self.assertEqual(warnings, "")
        self.assertEqual(counts, {"sneaker": 1})
        self.assertEqual(
            Sneaker.objects.get(user=self.other).catalog_product, self.product
        )
