"""Tests for the renderer seam."""

import matplotlib.pyplot as plt
from django.test import SimpleTestCase, override_settings

from incentives.analysis import detect_anomalies
from incentives.domain import ChartData
from incentives.renderers import (
    MAX_SERIES,
    SERIES_COLOURS,
    ChartRenderer,
    MatplotlibPngRenderer,
    UnknownRenderer,
    get_renderer,
)
from incentives.tests.factories import make_series

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def chart(series, **kwargs):
    return ChartData(
        title="Incentive over time",
        subtitle="a window",
        x_label="Time (UTC)",
        y_label="Incentive",
        series=tuple(series),
        **kwargs,
    )


class MatplotlibPngRendererTests(SimpleTestCase):
    def setUp(self):
        plt.close("all")
        self.addCleanup(plt.close, "all")
        self.renderer = MatplotlibPngRenderer()

    def test_renders_png_bytes(self):
        image = self.renderer.render(chart([make_series(1, [0.1, 0.2, 0.3])]))

        self.assertTrue(image.startswith(PNG_MAGIC))

    def test_declares_its_content_type(self):
        self.assertEqual(self.renderer.content_type, "image/png")

    def test_does_not_leak_figures(self):
        for _ in range(3):
            self.renderer.render(chart([make_series(1, [0.1, 0.2])]))

        self.assertEqual(plt.get_fignums(), [])

    def test_closes_the_figure_even_when_rendering_fails(self):
        broken = chart([make_series(1, [0.1, 0.2])])
        with self.assertRaises(ValueError):
            # An invalid dpi makes savefig fail deep inside the backend.
            MatplotlibPngRenderer(width=-1, height=-1).render(broken)

        self.assertEqual(plt.get_fignums(), [])

    def test_all_zero_values_still_render(self):
        image = self.renderer.render(chart([make_series(1, [0.0, 0.0, 0.0])]))

        self.assertTrue(image.startswith(PNG_MAGIC))

    def test_renders_anomaly_highlights(self):
        series = make_series(7, [0.10, 0.11, 0.10, 0.11, 0.10, 0.95, 0.96, 0.95])
        anomalies = detect_anomalies([series])

        image = self.renderer.render(chart([series], highlights=anomalies))

        self.assertTrue(image.startswith(PNG_MAGIC))

    def test_renders_with_an_other_series_count(self):
        image = self.renderer.render(
            chart(
                [make_series(1, [0.1, 0.2]), make_series(2, [0.3, 0.4])],
                other_series_count=250,
            )
        )

        self.assertTrue(image.startswith(PNG_MAGIC))

    def test_palette_is_fixed_and_never_cycled(self):
        self.assertEqual(len(SERIES_COLOURS), MAX_SERIES)
        self.assertEqual(len(set(SERIES_COLOURS)), MAX_SERIES)


class RendererRegistryTests(SimpleTestCase):
    def test_resolves_the_configured_renderer(self):
        self.assertIsInstance(get_renderer(), MatplotlibPngRenderer)

    @override_settings(INCENTIVES_RENDERER="matplotlib_png")
    def test_explicit_name_wins(self):
        self.assertIsInstance(get_renderer("matplotlib_png"), MatplotlibPngRenderer)

    def test_unknown_renderer_fails_loudly(self):
        with self.assertRaises(UnknownRenderer):
            get_renderer("ascii-art")

    def test_shipped_renderer_satisfies_the_protocol(self):
        self.assertIsInstance(MatplotlibPngRenderer(), ChartRenderer)
