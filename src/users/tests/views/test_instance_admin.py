from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.tasks import count_prunable_items
from users.instance_admin import TEMP_PASSWORD_SESSION_KEY

User = get_user_model()


def create_account(username, **extra):
    """Create an account with the shared test password."""
    credentials = {"username": username, "password": "12345"}
    return User.objects.create_user(**credentials, **extra)


class InstanceAdminTestCase(TestCase):
    """An admin (the first account) and an ordinary second account."""

    def setUp(self):
        """Create both accounts and log in as the admin."""
        cache.clear()
        # the page pings the Celery worker; there is no broker under test
        self.enterContext(
            patch(
                "users.instance_admin.ping_celery_workers",
                return_value=["celery@test"],
            ),
        )
        self.admin_credentials = {"username": "owner", "password": "12345"}
        self.admin = User.objects.create_user(**self.admin_credentials)
        self.other_credentials = {"username": "second", "password": "12345"}
        self.other = User.objects.create_user(**self.other_credentials)
        self.client.login(**self.admin_credentials)

    def user_action(self, user, action):
        """Post one action from the user list."""
        return self.client.post(
            reverse("instance_admin_user_action", args=[user.id]),
            {"user_action": action},
        )


class OwnershipTests(TestCase):
    """Ownership is a stored flag given to the first real account."""

    def test_first_account_becomes_admin(self):
        """Only the first account created is flagged staff."""
        first = create_account("first")
        second = create_account("second")

        self.assertTrue(first.is_staff)
        self.assertTrue(first.is_instance_admin)
        self.assertFalse(second.is_staff)
        self.assertFalse(second.is_instance_admin)

    def test_demo_account_does_not_take_ownership(self):
        """A demo account created first leaves ownership for a real one."""
        demo = create_account("demo", is_demo=True)
        real = create_account("real")

        self.assertFalse(demo.is_staff)
        self.assertTrue(real.is_instance_admin)

    def test_ownership_does_not_move_when_the_owner_is_deactivated(self):
        """Deactivating the first account no longer promotes the next oldest."""
        first = create_account("first")
        second = create_account("second")

        first.is_active = False
        first.save()

        self.assertFalse(first.is_instance_admin)
        self.assertFalse(second.is_instance_admin)


class AdminPageAccessTests(InstanceAdminTestCase):
    """Only instance admins reach the page or its actions."""

    def test_admin_sees_the_page_and_nav_entry(self):
        """The page renders every section for an admin."""
        response = self.client.get(reverse("instance_admin"))

        self.assertEqual(response.status_code, 200)
        for heading in ("Users", "Caches", "Housekeeping", "Scheduled Tasks", "Health"):
            self.assertContains(response, heading)
        self.assertContains(response, f'href="{reverse("instance_admin")}"')
        self.assertContains(response, f'action="{reverse("clear_search_cache")}"')
        self.assertContains(response, f'action="{reverse("purge_image_cache")}"')

    def test_other_accounts_are_refused(self):
        """An ordinary account gets a 403 and no nav entry."""
        self.client.logout()
        self.client.login(**self.other_credentials)

        self.assertEqual(self.client.get(reverse("instance_admin")).status_code, 403)
        self.assertEqual(self.user_action(self.admin, "deactivate").status_code, 403)
        response = self.client.post(
            reverse("instance_admin_run_task"),
            {"task": "prune_unreferenced_items"},
        )
        self.assertEqual(response.status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

        response = self.client.get(reverse("account"))
        self.assertNotContains(response, reverse("instance_admin"))

    def test_demo_admin_is_refused(self):
        """A demo account never gets in, even with the staff flag."""
        self.admin.is_demo = True
        self.admin.save()

        self.assertEqual(self.client.get(reverse("instance_admin")).status_code, 403)

    @override_settings(REGISTRATION=False)
    def test_registration_state_is_shown(self):
        """The page reports whether sign-up is open."""
        response = self.client.get(reverse("instance_admin"))

        self.assertFalse(response.context["registration_open"])
        self.assertContains(response, "nobody new can sign up")


class UserListTests(InstanceAdminTestCase):
    """The user list and its actions."""

    def test_item_counts(self):
        """Each account's tracked items are counted."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(item=item, user=self.other, status=Status.PLANNING.value)

        response = self.client.get(reverse("instance_admin"))

        counts = {row.username: row.item_count for row in response.context["user_rows"]}
        self.assertEqual(counts, {"owner": 0, "second": 1})

    def test_deactivate_and_reactivate(self):
        """A deactivated account can't sign in until it is reactivated."""
        self.user_action(self.other, "deactivate")
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(**self.other_credentials))

        self.client.login(**self.admin_credentials)
        self.user_action(self.other, "activate")
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_active)

    def test_promote_and_demote(self):
        """Admin rights can be given and taken away."""
        self.user_action(self.other, "promote")
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_instance_admin)

        self.user_action(self.other, "demote")
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_instance_admin)

    def test_demote_clears_superuser(self):
        """Removing admin from a superuser really removes it."""
        self.other.is_superuser = True
        self.other.save()

        self.user_action(self.other, "demote")

        self.other.refresh_from_db()
        self.assertFalse(self.other.is_instance_admin)

    def test_demo_account_cannot_be_promoted(self):
        """A demo account is never made admin."""
        self.other.is_demo = True
        self.other.save()

        self.user_action(self.other, "promote")

        self.other.refresh_from_db()
        self.assertFalse(self.other.is_staff)

    def test_own_account_cannot_be_changed(self):
        """An admin can't demote or deactivate themselves into a lockout."""
        for action in ("deactivate", "demote", "reset_password"):
            response = self.user_action(self.admin, action)
            messages = [str(m) for m in get_messages(response.wsgi_request)]
            self.assertTrue(any("your own account" in m for m in messages))

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_instance_admin)
        self.assertTrue(self.admin.check_password("12345"))

    def test_unknown_user_is_404(self):
        """Acting on a missing account is a 404."""
        response = self.client.post(
            reverse("instance_admin_user_action", args=[9999]),
            {"user_action": "deactivate"},
        )
        self.assertEqual(response.status_code, 404)


class TemporaryPasswordTests(InstanceAdminTestCase):
    """Resetting another account's password."""

    def reset_other(self):
        """Reset the second account and return its temporary password."""
        self.user_action(self.other, "reset_password")
        return self.client.session[TEMP_PASSWORD_SESSION_KEY]["password"]

    def test_reset_shows_the_password_once(self):
        """The temporary password is on the next page load only."""
        temporary_password = self.reset_other()

        response = self.client.get(reverse("instance_admin"))
        self.assertContains(response, temporary_password)
        response = self.client.get(reverse("instance_admin"))
        self.assertNotContains(response, temporary_password)

        self.other.refresh_from_db()
        self.assertTrue(self.other.must_change_password)
        self.assertTrue(self.other.check_password(temporary_password))
        self.assertFalse(self.other.check_password("12345"))

    def test_demo_password_is_not_reset(self):
        """The demo account keeps its password."""
        self.other.is_demo = True
        self.other.save()

        self.user_action(self.other, "reset_password")

        self.other.refresh_from_db()
        self.assertTrue(self.other.check_password("12345"))
        self.assertFalse(self.other.must_change_password)

    def test_account_is_held_until_the_password_is_changed(self):
        """Every page redirects to the change form; changing it lifts the hold."""
        temporary_password = self.reset_other()
        self.client.logout()
        self.client.login(username="second", password=temporary_password)
        change_url = reverse("change_temporary_password")

        self.assertRedirects(self.client.get(reverse("home")), change_url)
        self.assertRedirects(self.client.post(reverse("regenerate_token")), change_url)
        response = self.client.get(reverse("home"), headers={"HX-Request": "true"})
        self.assertEqual(response["HX-Redirect"], change_url)
        self.assertEqual(self.client.get(change_url).status_code, 200)

        response = self.client.post(
            change_url,
            {"new_password1": "a-Brand-new-pw-91", "new_password2": "mismatch"},
        )
        self.assertEqual(response.status_code, 200)
        self.other.refresh_from_db()
        self.assertTrue(self.other.must_change_password)

        response = self.client.post(
            change_url,
            {
                "new_password1": "a-Brand-new-pw-91",
                "new_password2": "a-Brand-new-pw-91",
            },
        )
        self.assertRedirects(response, reverse("home"))
        self.other.refresh_from_db()
        self.assertFalse(self.other.must_change_password)
        self.assertTrue(self.other.check_password("a-Brand-new-pw-91"))
        # still signed in, and no longer held
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_logout_stays_reachable(self):
        """A held account can still sign out."""
        temporary_password = self.reset_other()
        self.client.logout()
        self.client.login(username="second", password=temporary_password)

        response = self.client.post(reverse("account_logout"))

        self.assertNotEqual(
            response.headers.get("Location"),
            reverse("change_temporary_password"),
        )

    def test_change_page_is_only_for_held_accounts(self):
        """Without a temporary password the page just goes home."""
        response = self.client.get(reverse("change_temporary_password"))

        self.assertRedirects(response, reverse("home"))


class MaintenanceTests(InstanceAdminTestCase):
    """Housekeeping numbers and running scheduled tasks."""

    def test_prunable_count(self):
        """Unreferenced items are counted; tracked ones are not."""
        with patch("app.tasks.cache_item_image.delay"):
            Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Unused",
                image="http://example.com/a.jpg",
            )
            tracked = Item.objects.create(
                media_id="2",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title="Tracked",
                image="http://example.com/b.jpg",
            )
        Movie.objects.create(
            item=tracked,
            user=self.other,
            status=Status.PLANNING.value,
        )

        response = self.client.get(reverse("instance_admin"))

        self.assertEqual(response.context["item_count"], 2)
        self.assertEqual(response.context["prunable_item_count"], 1)

    def test_prune_resets_the_cached_count(self):
        """Running the prune from the page drops the unused rows and the count."""
        with patch("app.tasks.cache_item_image.delay"):
            Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Unused",
                image="http://example.com/a.jpg",
            )
        self.assertEqual(count_prunable_items(), 1)

        response = self.client.post(
            reverse("instance_admin_run_task"),
            {"task": "prune_unreferenced_items"},
        )

        self.assertRedirects(response, reverse("instance_admin"))
        self.assertEqual(Item.objects.count(), 0)
        self.assertEqual(count_prunable_items(), 0)

    def test_run_task_queues_the_scheduled_task(self):
        """A task from the beat schedule is queued by its registered name."""
        with patch("events.tasks.reload_calendar.delay") as delay:
            self.client.post(
                reverse("instance_admin_run_task"),
                {"task": "reload_calendar"},
            )

        delay.assert_called_once_with()

    def test_run_task_refuses_anything_else(self):
        """Notification senders and unknown names are not run."""
        with patch("events.tasks.send_daily_digest_notifications.delay") as delay:
            for task in ("send_daily_digest", "Purge cached images", ""):
                response = self.client.post(
                    reverse("instance_admin_run_task"),
                    {"task": task},
                )
                messages = [str(m) for m in get_messages(response.wsgi_request)]
                self.assertIn("That task can't be run from here.", messages)

        delay.assert_not_called()

    def test_scheduled_tasks_are_listed(self):
        """Every beat entry shows up, with run-now only where allowed."""
        response = self.client.get(reverse("instance_admin"))

        tasks = {task["key"]: task for task in response.context["scheduled_tasks"]}
        self.assertIn("prune_unreferenced_items", tasks)
        self.assertEqual(tasks["reload_calendar"]["frequency"], "Every day")
        self.assertEqual(tasks["backfill_item_genres"]["frequency"], "Every 7 days")
        self.assertTrue(tasks["reload_calendar"]["runnable"])
        self.assertFalse(tasks["send_daily_digest"]["runnable"])
        keys = [task["key"] for task in response.context["scheduled_tasks"]]
        self.assertEqual(
            keys[-2:],
            ["send_release_notifications", "send_daily_digest"],
        )

    def test_health(self):
        """Health reports the cache, the worker and the database."""
        response = self.client.get(reverse("instance_admin"))

        health = response.context["health"]
        self.assertTrue(health["redis_ok"])
        self.assertTrue(health["celery_ok"])
        self.assertEqual(health["database_vendor"], "sqlite")
