"""The documented environment variables are proven to take effect."""

import importlib
import os
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class ProjectConfigurationTests(SimpleTestCase):
    def test_django_system_checks_pass(self):
        call_command("check")

    def test_incentives_settings_are_present_and_typed(self):
        from django.conf import settings

        self.assertIsInstance(settings.GRAPHQL_API_URL, str)
        self.assertTrue(settings.GRAPHQL_API_URL.startswith("http"))
        self.assertIsInstance(settings.GRAPHQL_SUBNET_UID, int)
        self.assertIsInstance(settings.GRAPHQL_REQUEST_TIMEOUT, float)
        self.assertIsInstance(settings.GRAPHQL_MAX_RETRIES, int)
        self.assertIsInstance(settings.GRAPHQL_RETRY_BACKOFF, float)
        self.assertIsInstance(settings.INCENTIVES_CACHE_TTL, int)
        self.assertIsInstance(settings.INCENTIVES_TOP_N, int)

    def test_whitenoise_static_storage_is_configured_via_storages(self):
        from django.conf import settings

        # STATICFILES_STORAGE was removed in Django 5.1; STORAGES is the
        # supported spelling.
        self.assertIn("whitenoise", settings.STORAGES["staticfiles"]["BACKEND"])

    def test_no_database_is_configured(self):
        from django.conf import settings

        # This project is a GraphQL client: no models, no migrations, no DB.
        # Django backfills a dummy backend when DATABASES is left empty.
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.dummy")


class SettingsEnvironmentTests(SimpleTestCase):
    """
    The settings module is reloaded with a patched environment so the
    documented variables are actually proven to take effect.
    """

    def _reload_settings(self, env):
        import graphql.settings as settings_module

        with patch.dict(os.environ, env, clear=False):
            return importlib.reload(settings_module)

    def tearDown(self):
        import graphql.settings as settings_module

        # Restore the module to the ambient environment for later tests.
        importlib.reload(settings_module)

    def test_debug_is_off_unless_explicitly_enabled(self):
        self.assertFalse(self._reload_settings({"DJANGO_DEBUG": "False"}).DEBUG)
        self.assertFalse(self._reload_settings({"DJANGO_DEBUG": "nonsense"}).DEBUG)
        self.assertTrue(self._reload_settings({"DJANGO_DEBUG": "True"}).DEBUG)
        self.assertTrue(self._reload_settings({"DJANGO_DEBUG": "1"}).DEBUG)

    def test_allowed_hosts_is_split_and_stripped(self):
        module = self._reload_settings({"DJANGO_ALLOWED_HOSTS": "a.test, b.test ,"})

        self.assertEqual(module.ALLOWED_HOSTS, ["a.test", "b.test"])

    def test_secret_key_comes_from_the_environment(self):
        self.assertEqual(
            self._reload_settings({"DJANGO_SECRET_KEY": "from-env"}).SECRET_KEY,
            "from-env",
        )

    def test_graphql_settings_come_from_the_environment(self):
        module = self._reload_settings(
            {
                "GRAPHQL_API_URL": "https://other.test/graphql",
                "GRAPHQL_SUBNET_UID": "42",
                "GRAPHQL_REQUEST_TIMEOUT": "2.5",
                "GRAPHQL_MAX_RETRIES": "5",
            }
        )

        self.assertEqual(module.GRAPHQL_API_URL, "https://other.test/graphql")
        self.assertEqual(module.GRAPHQL_SUBNET_UID, 42)
        self.assertEqual(module.GRAPHQL_REQUEST_TIMEOUT, 2.5)
        self.assertEqual(module.GRAPHQL_MAX_RETRIES, 5)

    def test_malformed_numeric_settings_fall_back_to_the_default(self):
        module = self._reload_settings({"GRAPHQL_SUBNET_UID": "not-a-number"})

        self.assertEqual(module.GRAPHQL_SUBNET_UID, 18)

    def test_redis_url_switches_the_cache_backend(self):
        module = self._reload_settings({"REDIS_URL": "redis://cache:6379/0"})

        self.assertIn("redis", module.CACHES["default"]["BACKEND"].lower())
        self.assertEqual(module.CACHES["default"]["LOCATION"], "redis://cache:6379/0")

    def test_locmem_is_the_default_cache_backend(self):
        module = self._reload_settings({"REDIS_URL": ""})

        self.assertIn("locmem", module.CACHES["default"]["BACKEND"].lower())

    def test_source_and_renderer_are_selectable(self):
        module = self._reload_settings(
            {"INCENTIVES_SOURCE": "SAMPLE", "INCENTIVES_RENDERER": "matplotlib_png"}
        )

        self.assertEqual(module.INCENTIVES_SOURCE, "sample")
        self.assertEqual(module.INCENTIVES_RENDERER, "matplotlib_png")

    def test_secure_cookies_are_enabled_only_outside_debug(self):
        production = self._reload_settings({"DJANGO_DEBUG": "False"})
        self.assertTrue(production.SESSION_COOKIE_SECURE)
        self.assertTrue(production.CSRF_COOKIE_SECURE)

        development = self._reload_settings({"DJANGO_DEBUG": "True"})
        self.assertFalse(development.SESSION_COOKIE_SECURE)
        self.assertFalse(development.CSRF_COOKIE_SECURE)
        self.assertIsNone(development.SECURE_PROXY_SSL_HEADER)
