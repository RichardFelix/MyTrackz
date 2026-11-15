from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from app import provider_keys
from app.models import ProviderKey

TMDB = provider_keys.PROVIDERS_BY_SLUG["tmdb"]
IGDB = provider_keys.PROVIDERS_BY_SLUG["igdb"]
HARDCOVER = provider_keys.PROVIDERS_BY_SLUG["hardcover"]


def http_error(status_code):
    """Return the error requests raises for a response with that status."""
    response = MagicMock(status_code=status_code)
    return requests.exceptions.HTTPError(response=response)


@override_settings(TMDB_API="from-settings", KICKSDB_API="")
class ProviderKeyStoreTests(TestCase):
    """Saved keys win over settings and are stored encrypted."""

    def setUp(self):
        """Start from an empty key cache."""
        cache.clear()

    def test_falls_back_to_settings(self):
        """With nothing saved the setting is returned."""
        self.assertEqual(provider_keys.get("TMDB_API"), "from-settings")
        self.assertNotEqual(
            provider_keys.source("TMDB_API"),
            provider_keys.SOURCE_SAVED,
        )

    def test_saved_key_wins_and_is_encrypted(self):
        """A saved key is returned, and is not stored as typed."""
        provider_keys.save(TMDB, {"TMDB_API": "  my-own-tmdb-key  "})

        self.assertEqual(provider_keys.get("TMDB_API"), "my-own-tmdb-key")
        self.assertEqual(provider_keys.source("TMDB_API"), provider_keys.SOURCE_SAVED)
        self.assertEqual(provider_keys.hint("TMDB_API"), "-key")
        stored = ProviderKey.objects.get(name="TMDB_API").value
        self.assertNotIn("my-own-tmdb-key", stored)

    def test_blank_value_keeps_saved_key(self):
        """Saving an empty field leaves the stored key alone."""
        provider_keys.save(TMDB, {"TMDB_API": "my-own-tmdb-key"})
        changed = provider_keys.save(TMDB, {"TMDB_API": ""})

        self.assertFalse(changed)
        self.assertEqual(provider_keys.get("TMDB_API"), "my-own-tmdb-key")

    def test_clear_goes_back_to_settings(self):
        """Removing the saved key restores the setting."""
        provider_keys.save(TMDB, {"TMDB_API": "my-own-tmdb-key"})
        provider_keys.clear(TMDB)

        self.assertEqual(provider_keys.get("TMDB_API"), "from-settings")

    def test_unreadable_saved_key_falls_back(self):
        """A key saved under another SECRET is ignored, not an error."""
        provider_keys.save(TMDB, {"TMDB_API": "my-own-tmdb-key"})
        cache.clear()

        with override_settings(SECRET_KEY="a-different-secret"):  # noqa: S106
            self.assertEqual(provider_keys.get("TMDB_API"), "from-settings")

    def test_saving_drops_cached_access_token(self):
        """New IGDB credentials must not keep using the old token."""
        cache.set("igdb_access_token", "old-token")
        provider_keys.save(IGDB, {"IGDB_ID": "id", "IGDB_SECRET": "secret"})

        self.assertIsNone(cache.get("igdb_access_token"))

    def test_hardcover_token_gets_bearer_prefix(self):
        """Hardcover's token is stored the way the provider sends it."""
        provider_keys.save(HARDCOVER, {"HARDCOVER_API": "abc.def.ghi"})

        self.assertEqual(provider_keys.get("HARDCOVER_API"), "Bearer abc.def.ghi")

    def test_missing_key_reported(self):
        """A provider with no key anywhere reads as not set."""
        row = provider_keys.status(provider_keys.PROVIDERS_BY_SLUG["kicksdb"])

        self.assertEqual(row["source"], provider_keys.SOURCE_NONE)

    def test_every_provider_has_a_connection_test(self):
        """The Test button exists for each listed provider."""
        self.assertEqual(
            set(provider_keys.TESTS),
            {provider.slug for provider in provider_keys.PROVIDERS},
        )


@override_settings(TMDB_API="from-settings", KICKSDB_API="")
class ConnectionTestTests(TestCase):
    """The Test connection button tries the typed value without saving it."""

    def setUp(self):
        """Start from an empty key cache and a stubbed provider session."""
        cache.clear()
        self.request = self.enterContext(
            patch("app.providers.services.session.request"),
        )

    def test_typed_value_is_sent(self):
        """The candidate key is used, and nothing is stored."""
        ok, message = provider_keys.test_connection(TMDB, {"TMDB_API": "typed"})

        self.assertTrue(ok)
        self.assertIn("TMDB", message)
        self.assertEqual(self.request.call_args.kwargs["params"]["api_key"], "typed")
        self.assertFalse(ProviderKey.objects.exists())

    def test_blank_tests_key_in_use(self):
        """An empty field tests whatever is in effect now."""
        provider_keys.test_connection(TMDB, {"TMDB_API": ""})

        self.assertEqual(
            self.request.call_args.kwargs["params"]["api_key"],
            "from-settings",
        )

    def test_rejected_key(self):
        """A 401 reads as a rejected key and does not echo it."""
        self.request.return_value.raise_for_status.side_effect = http_error(401)

        ok, message = provider_keys.test_connection(TMDB, {"TMDB_API": "typed"})

        self.assertFalse(ok)
        self.assertIn("rejected", message)
        self.assertNotIn("typed", message)

    def test_unreachable(self):
        """A network failure is reported as such."""
        self.request.side_effect = requests.exceptions.ConnectionError

        ok, message = provider_keys.test_connection(TMDB, {"TMDB_API": "typed"})

        self.assertFalse(ok)
        self.assertIn("Could not reach", message)

    def test_nothing_to_test(self):
        """No request is made when there is no key at all."""
        kicksdb = provider_keys.PROVIDERS_BY_SLUG["kicksdb"]

        ok, _ = provider_keys.test_connection(kicksdb, {})

        self.assertFalse(ok)
        self.request.assert_not_called()

    def test_comicvine_error_in_body(self):
        """Comic Vine answers 200 with an error code for a bad key."""
        self.request.return_value.json.return_value = {"status_code": 100}
        comicvine = provider_keys.PROVIDERS_BY_SLUG["comicvine"]

        ok, _ = provider_keys.test_connection(comicvine, {"COMICVINE_API": "typed"})

        self.assertFalse(ok)
