from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


class PwaEndpointTests(TestCase):
    """Verify public PWA metadata, worker delivery, and install UI wiring."""

    def test_manifest_is_public_and_installable(self):
        """The public manifest provides Chrome's core install metadata."""
        response = self.client.get(reverse("webmanifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        self.assertEqual(response["Cache-Control"], "public, max-age=3600")

        manifest = response.json()
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertNotIn("orientation", manifest)
        self.assertFalse(manifest["prefer_related_applications"])
        self.assertEqual(
            {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]},
            {
                ("192x192", "any"),
                ("512x512", "any"),
                ("192x192", "maskable"),
                ("512x512", "maskable"),
            },
        )
        self.assertEqual(
            [shortcut["name"] for shortcut in manifest["shortcuts"]],
            ["Home", "Discover", "Trending", "Search"],
        )
        self.assertTrue(
            any(
                "form_factor" not in screenshot
                for screenshot in manifest["screenshots"]
            ),
        )

    @override_settings(STATIC_URL="/mytrackz/static/")
    def test_manifest_honors_a_deployment_url_prefix(self):
        """Manifest navigation and asset URLs remain inside BASE_URL."""
        response = self.client.get(
            "/manifest.webmanifest",
            SCRIPT_NAME="/mytrackz",
        )

        self.assertEqual(response.status_code, 200)
        manifest = response.json()
        self.assertEqual(manifest["id"], "/mytrackz/")
        self.assertEqual(manifest["start_url"], "/mytrackz/")
        self.assertEqual(manifest["scope"], "/mytrackz/")
        self.assertTrue(
            all(
                icon["src"].startswith("/mytrackz/static/")
                for icon in manifest["icons"]
            ),
        )
        self.assertTrue(
            all(
                shortcut["url"].startswith("/mytrackz/")
                for shortcut in manifest["shortcuts"]
            ),
        )

    def test_service_worker_is_public_and_not_http_cached(self):
        """The root worker updates promptly and never caches app pages."""
        response = self.client.get(reverse("service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        content = response.content.decode()
        self.assertIn("event.request.mode === 'navigate'", content)
        self.assertIn("scopedUrl('offline/')", content)
        self.assertNotIn("scopedUrl('/')", content)

    def test_offline_fallback_is_public_and_non_personalized(self):
        """Offline fallback does not contain session-specific content."""
        response = self.client.get(reverse("offline"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "offline")
        self.assertNotContains(response, "Sign out")
        self.assertNotContains(response, "{{")
        self.assertNotContains(response, "{%")

    def test_base_template_preserves_ios_metadata_and_uses_root_worker(self):
        """Every base page advertises iOS standalone and root PWA URLs."""
        response = self.client.get(reverse("account_login"))

        self.assertContains(
            response,
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            html=True,
        )
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, "apple-mobile-web-app-status-bar-style")
        self.assertContains(response, 'href="/manifest.webmanifest"')
        self.assertContains(response, 'data-service-worker-url="/serviceworker.js"')
        self.assertContains(response, 'data-service-worker-scope="/"')

    def test_about_page_has_progressively_enhanced_install_action(self):
        """Settings exposes the install action for supported browsers."""
        user = get_user_model().objects.create_user(username="pwa-test")
        user.set_unusable_password()
        user.save(update_fields=["password"])
        self.client.force_login(user)

        response = self.client.get(reverse("about"))

        self.assertContains(response, 'id="pwa-install-section"')
        self.assertContains(response, "data-pwa-install")
        self.assertContains(response, "Install MyTrackz")
