"""Collection CSVs must preserve build minutes, pair data and offline catalogs."""

import csv
import io
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import (
    Game,
    Item,
    Lego,
    MediaTypes,
    Sneaker,
    SneakerProduct,
    Sources,
    Status,
)
from app.providers import sneakers
from integrations.exports import generate_rows
from integrations.imports import yamtrack


class CollectionRoundTripTests(TestCase):
    """Use isolated data without touching the real collection or providers."""

    def setUp(self):
        """Create a mixed collection with an independently edited sneaker."""
        self.owner = get_user_model().objects.create_user(username="collection-owner")
        self.destination = get_user_model().objects.create_user(
            username="collection-copy"
        )
        for target in [
            "app.tasks.cache_item_image.delay",
            "app.models.Item.fetch_releases",
            "app.models.Lego.process_progress",
        ]:
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.product = SneakerProduct.objects.create(
            catalog_id="goat-719082",
            name="Catalog sneaker",
            brand="Nike",
            style_code="DD1391-100",
            colorway="White/Black",
            image="https://example.com/catalog.jpg",
            source_url="https://www.goat.com/sneakers/dunk-low-black-white-dd1391-100",
            release_date=date(2021, 3, 10),
            reported_retail_price=Decimal("115.00"),
            reported_currency="USD",
        )
        self.shoe = Sneaker.objects.create(
            item=self.item("sneaker", Sources.KICKSDB, self.product.pk),
            user=self.owner,
            progress=2,
            status=Status.IN_PROGRESS,
            brand="Personal brand note",
            colorway="Personal color note",
            original_msrp=Decimal("100.00"),
            msrp_currency="USD",
            notes='Deadstock, "second pair"\nLast worn: Sep 20\nKeep both pairs.',
        )
        self.manual = Sneaker.objects.create(
            item=self.item("sneaker", Sources.MANUAL),
            user=self.owner,
            progress=1,
            status=Status.PLANNING,
            brand="Supra",
            style_code="S73020G",
            colorway="Grey/Gum",
        )
        self.build = Lego.objects.create(
            item=self.item("lego", Sources.REBRICKABLE, "75192-1"),
            user=self.owner,
            progress=95,
            status=Status.IN_PROGRESS,
            notes='Bag 1\nBag 2, "finished"',
        )
        self.game = Game.objects.create(
            item=self.item("game", Sources.MANUAL),
            user=self.owner,
            progress=125,
            status=Status.PLANNING,
        )

    def item(self, kind, source, identity=None):
        """Create an identity in the test database only."""
        return Item.objects.create(
            media_type=kind,
            source=source,
            media_id=identity or Item.generate_manual_id(),
            title=f"Test {kind}",
            image=settings.IMG_NONE,
        )

    def export(self):
        """Read the real streaming CSV exporter."""
        return "".join(generate_rows(self.owner)).encode()

    def test_mixed_collection_roundtrip_and_overwrite(self):
        """New and overwrite modes preserve collection fields and game time."""
        data = self.export()
        for mode in ["new", "overwrite"]:
            with (
                self.subTest(mode=mode),
                patch(
                    "app.providers.services.api_request",
                    side_effect=AssertionError("Complete exports must import offline"),
                ),
            ):
                counts, warnings = yamtrack.importer(
                    io.BytesIO(data), self.destination, mode
                )
                self.assertEqual(warnings, "")
                self.assertEqual(counts, {"game": 1, "lego": 1, "sneaker": 2})
                self.assertEqual(Lego.objects.get(user=self.destination).progress, 95)
                self.assertEqual(
                    Lego.objects.get(user=self.destination).notes, self.build.notes
                )
                self.assertEqual(Game.objects.get(user=self.destination).progress, 125)
                copy = Sneaker.objects.get(user=self.destination, item=self.shoe.item)
                for field in [
                    "progress",
                    "status",
                    "notes",
                    "brand",
                    "style_code",
                    "colorway",
                    "release_date",
                    "original_msrp",
                    "msrp_currency",
                ]:
                    self.assertEqual(
                        getattr(copy, field), getattr(self.shoe, field), field
                    )
                self.assertEqual(copy.catalog_product, self.product)
                self.assertTrue(copy.history.exists())
                custom = Sneaker.objects.get(
                    user=self.destination, item=self.manual.item
                )
                self.assertEqual(custom.status, Status.PLANNING)
                self.assertIsNone(custom.original_msrp)
                self.assertIsNone(custom.catalog_product)
        counts, warnings = yamtrack.importer(io.BytesIO(data), self.destination, "new")
        self.assertEqual((counts, warnings), ({}, ""))

    def test_snapshot_restores_on_fresh_install_without_api(self):
        """A snapshot must be carried by CSV, independent of the source DB."""
        data = self.export()
        expected = {
            f.name: getattr(self.product, f.name) for f in SneakerProduct._meta.fields
        }
        self.shoe.item.delete()
        self.product.delete()
        with patch(
            "app.providers.services.api_request", side_effect=AssertionError("No API")
        ):
            counts, warnings = yamtrack.importer(
                io.BytesIO(data), self.destination, "new"
            )
            self.assertEqual(warnings, "")
            self.assertEqual(counts["sneaker"], 2)
            copy = Sneaker.objects.get(
                user=self.destination, item__media_id=expected["catalog_id"]
            )
            self.assertIsNotNone(copy.catalog_product)
            restored = sneakers.sneaker(expected["catalog_id"])["catalog_product"]
            for field, value in expected.items():
                self.assertEqual(getattr(restored, field), value, field)
            self.assertEqual(restored.brand, "Nike")
            self.assertEqual(copy.brand, "Personal brand note")

    def test_legacy_lego_integer_minutes(self):
        """Previous MyTrackz exports wrote LEGO progress as raw minutes."""
        rows = list(csv.DictReader(io.StringIO(self.export().decode())))
        for row in rows:
            if row["media_type"] == "lego":
                row["progress"] = "95"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        counts, warnings = yamtrack.importer(
            io.BytesIO(output.getvalue().encode()), self.destination, "new"
        )
        self.assertEqual(warnings, "")
        self.assertEqual(counts["lego"], 1)
        self.assertEqual(Lego.objects.get(user=self.destination).progress, 95)

    def csv_rows(self, rows):
        """Re-encode edited CSV rows for compatibility and validation checks."""
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode()

    def test_all_categories_still_import_together(self):
        """Collection columns do not interfere with the other media categories."""
        omitted = {"game", "lego", "sneaker", "season", "episode"}
        for kind in MediaTypes.values:
            if kind in omitted:
                continue
            model = apps.get_model("app", kind)
            model.objects.create(
                item=self.item(kind, Sources.MANUAL),
                user=self.owner,
                status=Status.PLANNING,
                notes='First line\r\nSecond, "quoted" line',
            )
        with patch(
            "app.providers.services.api_request", side_effect=AssertionError("No API")
        ):
            counts, warnings = yamtrack.importer(
                io.BytesIO(self.export()), self.destination, "new"
            )
        self.assertEqual(warnings, "")
        self.assertEqual(set(counts), set(MediaTypes.values) - {"season", "episode"})
        for kind in counts:
            model = apps.get_model("app", kind)
            originals = model.objects.filter(user=self.owner).order_by("item_id")
            copies = model.objects.filter(user=self.destination).order_by("item_id")
            self.assertEqual(
                list(originals.values_list("item_id", "status", "notes")),
                list(copies.values_list("item_id", "status", "notes")),
                kind,
            )

    def test_legacy_csv_without_snapshot_column(self):
        """Previously exported sneaker CSVs still attach existing snapshots."""
        rows = list(csv.DictReader(io.StringIO(self.export().decode())))
        for row in rows:
            row.pop("sneaker_catalog")
        counts, warnings = yamtrack.importer(
            io.BytesIO(self.csv_rows(rows)), self.destination, "new"
        )
        self.assertEqual(warnings, "")
        self.assertEqual(counts["sneaker"], 2)
        self.assertEqual(
            Sneaker.objects.get(
                user=self.destination, item=self.shoe.item
            ).catalog_product,
            self.product,
        )

    def test_import_never_overwrites_an_existing_snapshot(self):
        """Personal edits and imported claims cannot replace a saved catalog."""
        rows = list(csv.DictReader(io.StringIO(self.export().decode())))
        for row in rows:
            if row.get("sneaker_catalog"):
                product = json.loads(row["sneaker_catalog"])
                product["brand"] = "Changed imported brand"
                row["sneaker_catalog"] = json.dumps(product)
        _, warnings = yamtrack.importer(
            io.BytesIO(self.csv_rows(rows)), self.destination, "new"
        )
        self.assertEqual(warnings, "")
        self.product.refresh_from_db()
        self.assertEqual(self.product.brand, "Nike")

    def test_invalid_snapshot_does_not_delete_existing_entry(self):
        """A rejected overwrite row must leave the user's original shoe intact."""
        data = self.export()
        yamtrack.importer(io.BytesIO(data), self.destination, "new")
        original = Sneaker.objects.get(user=self.destination, item=self.shoe.item)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        for row in rows:
            if row.get("sneaker_catalog"):
                row["sneaker_catalog"] = '{"catalog_id": "wrong-identity"}'
        counts, warnings = yamtrack.importer(
            io.BytesIO(self.csv_rows(rows)), self.destination, "overwrite"
        )
        self.assertEqual(counts["sneaker"], 1)
        self.assertIn("identity does not match", warnings)
        original.refresh_from_db()
        self.assertEqual(original.notes, self.shoe.notes)
