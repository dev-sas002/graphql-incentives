"""Tests for the service layer: pipeline composition and caching."""

import base64
from unittest.mock import patch

import matplotlib.pyplot as plt
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from incentives import services
from incentives.sources import SampleIncentiveSource, UpstreamUnavailable

CACHE_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "service-tests",
    }
}


class FailingSource:
    name = "failing"

    def fetch(self, subnet_uid):
        raise UpstreamUnavailable("upstream is down")


@override_settings(
    INCENTIVES_SOURCE="sample",
    INCENTIVES_TOP_N=4,
    INCENTIVES_CACHE_TTL=300,
    AI_SUMMARY_ENABLED=False,
    ANTHROPIC_API_KEY="",
    CACHES=CACHE_SETTINGS,
)
class BuildReportTests(SimpleTestCase):
    def setUp(self):
        plt.close("all")
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(plt.close, "all")

    def test_produces_a_base64_png_and_statistics(self):
        report = services.build_report(18, 4)

        self.assertTrue(base64.b64decode(report.image_base64).startswith(b"\x89PNG"))
        self.assertEqual(report.content_type, "image/png")
        self.assertEqual(report.subnet_uid, 18)
        self.assertEqual(report.source_name, "sample")
        self.assertEqual(len(report.plotted_uids), 4)
        self.assertEqual(report.other_series_count, 20)
        self.assertEqual(report.stats.uid_count, 24)

    def test_summary_falls_back_to_the_computed_text(self):
        report = services.build_report(18, 4)

        self.assertEqual(report.summary_source, "rules")
        self.assertIn("Subnet 18", report.summary)

    def test_top_n_controls_how_many_series_are_plotted(self):
        self.assertEqual(len(services.build_report(18, 2).plotted_uids), 2)

    def test_upstream_failure_propagates(self):
        with self.assertRaises(UpstreamUnavailable):
            services.build_report(18, 4, source=FailingSource())

    def test_accepts_injected_collaborators(self):
        report = services.build_report(21, 3, source=SampleIncentiveSource(uid_count=5))

        self.assertEqual(report.stats.uid_count, 5)
        self.assertEqual(report.other_series_count, 2)


@override_settings(
    INCENTIVES_SOURCE="sample",
    INCENTIVES_TOP_N=4,
    INCENTIVES_CACHE_TTL=300,
    AI_SUMMARY_ENABLED=False,
    ANTHROPIC_API_KEY="",
    CACHES=CACHE_SETTINGS,
)
class ReportCacheTests(SimpleTestCase):
    def setUp(self):
        plt.close("all")
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(plt.close, "all")

    def test_first_call_renders_and_second_call_is_served_from_cache(self):
        with patch.object(services, "build_report", wraps=services.build_report) as spy:
            first, first_cached = services.get_report(18, 4)
            second, second_cached = services.get_report(18, 4)

        self.assertFalse(first_cached)
        self.assertTrue(second_cached)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(first.image_base64, second.image_base64)

    def test_a_different_subnet_is_a_different_cache_entry(self):
        services.get_report(18, 4)
        _, cached = services.get_report(21, 4)

        self.assertFalse(cached)

    def test_a_different_top_n_is_a_different_cache_entry(self):
        services.get_report(18, 4)
        _, cached = services.get_report(18, 3)

        self.assertFalse(cached)

    def test_cache_key_includes_source_and_renderer(self):
        key = services.cache_key(18, 4, "sample", "matplotlib_png")

        self.assertIn("sample", key)
        self.assertIn("matplotlib_png", key)
        self.assertTrue(key.startswith("incentives:v"))

    def test_invalidate_forces_the_next_call_to_re_render(self):
        services.get_report(18, 4)
        services.invalidate(18, 4)

        _, cached = services.get_report(18, 4)

        self.assertFalse(cached)

    def test_top_n_defaults_to_the_setting(self):
        report, _ = services.get_report(18)

        self.assertEqual(len(report.plotted_uids), 4)

    def test_a_failed_fetch_is_never_cached(self):
        with patch.object(services, "get_source", return_value=FailingSource()):
            with self.assertRaises(UpstreamUnavailable):
                services.get_report(18, 4)
            with self.assertRaises(UpstreamUnavailable):
                services.get_report(18, 4)
