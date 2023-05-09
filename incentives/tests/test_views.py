"""Tests for URL wiring, request validation and the rendered pages."""

from unittest.mock import patch

import matplotlib.pyplot as plt
from django.core.cache import cache
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from incentives import services, views
from incentives.sources import UpstreamUnavailable

CACHE_SETTINGS = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "view-tests",
    }
}

VIEW_SETTINGS = {
    "INCENTIVES_SOURCE": "sample",
    "GRAPHQL_SUBNET_UID": 18,
    "INCENTIVES_TOP_N": 4,
    "INCENTIVES_CACHE_TTL": 300,
    "AI_SUMMARY_ENABLED": False,
    "ANTHROPIC_API_KEY": "",
    "CACHES": CACHE_SETTINGS,
}


class UrlRoutingTests(SimpleTestCase):
    def test_named_routes_resolve_to_the_expected_paths(self):
        self.assertEqual(reverse("home"), "/")
        self.assertEqual(reverse("plot_view"), "/plot/")
        self.assertEqual(reverse("chart_image"), "/plot/chart.png")
        self.assertEqual(reverse("healthz"), "/healthz")


@override_settings(**VIEW_SETTINGS)
class HomeViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def test_renders_the_home_template(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "home.html")

    def test_links_to_the_chart(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, reverse("plot_view"))

    def test_offers_the_featured_subnets(self):
        response = self.client.get(reverse("home"))

        for subnet in views.FEATURED_SUBNETS:
            self.assertContains(response, f"?subnet={subnet}")


@override_settings(**VIEW_SETTINGS)
class PlotViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        plt.close("all")
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(plt.close, "all")

    def test_renders_the_dashboard(self):
        response = self.client.get(reverse("plot_view"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "plot.html")
        self.assertContains(response, "Subnet 18")
        self.assertContains(response, "UIDs tracked")

    def test_page_points_at_the_chart_image_endpoint(self):
        response = self.client.get(reverse("plot_view"))

        self.assertContains(response, f"{reverse('chart_image')}?subnet=18")

    def test_subnet_query_parameter_selects_the_subnet(self):
        response = self.client.get(reverse("plot_view"), {"subnet": 21})

        self.assertEqual(response.context["subnet_uid"], 21)
        self.assertContains(response, "Subnet 21")

    def test_defaults_to_the_configured_subnet(self):
        self.assertEqual(self.client.get(reverse("plot_view")).context["subnet_uid"], 18)

    def test_top_parameter_controls_the_series_count(self):
        response = self.client.get(reverse("plot_view"), {"top": 2})

        self.assertEqual(len(response.context["report"].plotted_uids), 2)

    def test_flagged_uids_that_are_not_plotted_are_marked_as_such(self):
        # Flagging runs subnet-wide, so with only two series drawn the table
        # will list UIDs the chart cannot ring. Those rows must say so.
        response = self.client.get(reverse("plot_view"), {"subnet": 21, "top": 2})

        plotted = set(response.context["report"].plotted_uids)
        shown = response.context["anomalies"]

        self.assertTrue(any(item.uid not in plotted for item in shown))
        self.assertContains(response, "not plotted")

    def test_top_parameter_is_clamped_to_the_palette_size(self):
        response = self.client.get(reverse("plot_view"), {"top": 99})

        self.assertEqual(response.context["top_n"], 8)

    def test_a_non_numeric_subnet_is_a_400(self):
        response = self.client.get(reverse("plot_view"), {"subnet": "../etc"})

        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "error.html")

    def test_an_out_of_range_subnet_is_a_400(self):
        self.assertEqual(
            self.client.get(reverse("plot_view"), {"subnet": 99999}).status_code, 400
        )

    def test_a_non_numeric_top_is_a_400(self):
        self.assertEqual(
            self.client.get(reverse("plot_view"), {"top": "many"}).status_code, 400
        )

    def test_reports_whether_the_page_was_served_from_cache(self):
        first = self.client.get(reverse("plot_view"))
        second = self.client.get(reverse("plot_view"))

        self.assertFalse(first.context["was_cached"])
        self.assertTrue(second.context["was_cached"])

    def test_refresh_invalidates_and_redirects(self):
        self.client.get(reverse("plot_view"))
        response = self.client.get(reverse("plot_view"), {"refresh": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/plot/?subnet=18&top=4")
        self.assertFalse(self.client.get(reverse("plot_view")).context["was_cached"])

    def test_an_unavailable_upstream_renders_the_error_page_with_503(self):
        with patch.object(
            services, "get_report", side_effect=UpstreamUnavailable("upstream is down")
        ):
            response = self.client.get(reverse("plot_view"))

        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, "error.html")
        self.assertContains(response, "Unable to fetch incentives data", status_code=503)
        self.assertContains(response, "upstream is down", status_code=503)


@override_settings(**VIEW_SETTINGS)
class ChartImageViewTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()
        plt.close("all")
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(plt.close, "all")

    def test_serves_png_bytes(self):
        response = self.client.get(reverse("chart_image"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_sets_a_cache_control_header_matching_the_ttl(self):
        response = self.client.get(reverse("chart_image"))

        self.assertEqual(response["Cache-Control"], "public, max-age=300")

    def test_shares_the_cache_with_the_page(self):
        self.client.get(reverse("plot_view"))

        with patch.object(services, "build_report") as spy:
            self.client.get(reverse("chart_image"), {"subnet": 18, "top": 4})

        spy.assert_not_called()

    def test_invalid_parameters_are_a_plain_text_400(self):
        response = self.client.get(reverse("chart_image"), {"subnet": "nope"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Type"], "text/plain")

    def test_an_unavailable_upstream_is_a_503(self):
        with patch.object(services, "get_report", side_effect=UpstreamUnavailable("down")):
            response = self.client.get(reverse("chart_image"))

        self.assertEqual(response.status_code, 503)


@override_settings(**VIEW_SETTINGS)
class HealthzTests(SimpleTestCase):
    def test_reports_ok_without_touching_the_upstream(self):
        response = Client().get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "source": "sample"})
