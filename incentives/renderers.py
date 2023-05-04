"""
Chart renderers — the second seam.

A renderer turns a :class:`~incentives.domain.ChartData` into bytes. It knows
nothing about HTTP, GraphQL or caching, so an SVG renderer, a sparkline
renderer or a CSV "renderer" can be added by writing one class and registering
it in ``RENDERERS``.

The matplotlib renderer follows a fixed eight-slot categorical palette rather
than matplotlib's default colour cycle: the slot order is chosen so adjacent
series stay distinguishable under the common colour-vision deficiencies, and a
ninth series is never given a generated colour — it is folded into the
"other UIDs" count instead (see ``analysis.select_top_series``).
"""

from __future__ import annotations

import io
from typing import Any, Protocol, runtime_checkable

import matplotlib

# The Agg backend must be selected *before* pyplot is imported, otherwise a GUI
# backend may already have been initialised and the server process will fail
# when it tries to render without a display.
matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402  (import must follow matplotlib.use)
import matplotlib.pyplot as plt  # noqa: E402
from django.conf import settings  # noqa: E402

from incentives.domain import ChartData  # noqa: E402

# Fixed categorical order. Never cycled, never extended at runtime.
SERIES_COLOURS = (
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
)
MAX_SERIES = len(SERIES_COLOURS)

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
STATUS_CRITICAL = "#d03b3b"

# Direct labels are readable up to four lines; past that they collide and the
# legend carries identity on its own.
DIRECT_LABEL_LIMIT = 4


@runtime_checkable
class ChartRenderer(Protocol):
    """The contract every renderer implements."""

    name: str
    content_type: str

    def render(self, chart: ChartData) -> bytes:
        """Return the encoded chart."""


class MatplotlibPngRenderer:
    """Renders a multi-series line chart to PNG bytes."""

    name = "matplotlib_png"
    content_type = "image/png"

    def __init__(self, width: float = 12.0, height: float = 5.6, dpi: int = 110):
        self.width = width
        self.height = height
        self.dpi = dpi

    def render(self, chart: ChartData) -> bytes:
        figure, axes = plt.subplots(
            figsize=(self.width, self.height), dpi=self.dpi, facecolor=SURFACE
        )
        try:
            axes.set_facecolor(SURFACE)
            self._draw_series(axes, chart)
            self._draw_anomalies(axes, chart)
            self._style_axes(axes, chart)
            self._draw_titles(figure, axes, chart)

            buffer = io.BytesIO()
            figure.savefig(
                buffer,
                format="png",
                facecolor=SURFACE,
                bbox_inches="tight",
                pad_inches=0.3,
            )
        finally:
            # Long-running server processes must not accumulate open figures.
            plt.close(figure)

        return buffer.getvalue()

    def _draw_series(self, axes: Any, chart: ChartData) -> None:
        plotted = chart.series[:MAX_SERIES]
        for index, series in enumerate(plotted):
            colour = SERIES_COLOURS[index]
            axes.plot(
                series.timestamps,
                series.values,
                label=f"UID {series.uid}",
                color=colour,
                linewidth=2.0,
                solid_capstyle="round",
                zorder=3 + index,
            )
            if len(plotted) <= DIRECT_LABEL_LIMIT and series.points:
                axes.annotate(
                    f"UID {series.uid}",
                    xy=(series.timestamps[-1], series.values[-1]),
                    xytext=(6, 0),
                    textcoords="offset points",
                    color=INK_SECONDARY,
                    fontsize=9,
                    va="center",
                )

    def _draw_anomalies(self, axes: Any, chart: ChartData) -> None:
        if not chart.highlights:
            return
        plotted_uids = {series.uid for series in chart.series[:MAX_SERIES]}
        visible = [item for item in chart.highlights if item.uid in plotted_uids]
        if not visible:
            return
        axes.scatter(
            [item.timestamp for item in visible],
            [item.value for item in visible],
            s=64,
            facecolors="none",
            edgecolors=STATUS_CRITICAL,
            linewidths=1.8,
            label="flagged step change",
            zorder=20,
        )

    def _style_axes(self, axes: Any, chart: ChartData) -> None:
        axes.grid(True, color=GRIDLINE, linewidth=1, axis="y")
        axes.set_axisbelow(True)
        for side in ("top", "right"):
            axes.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            axes.spines[side].set_color(BASELINE)
            axes.spines[side].set_linewidth(1)

        axes.tick_params(colors=INK_MUTED, labelsize=9, length=0)
        axes.set_xlabel(chart.x_label, color=INK_SECONDARY, fontsize=10, labelpad=8)
        axes.set_ylabel(chart.y_label, color=INK_SECONDARY, fontsize=10, labelpad=8)

        axes.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))

        values = [value for series in chart.series for value in series.values]
        ceiling = max(values) if values else 0.0
        axes.set_ylim(0, ceiling * 1.12 if ceiling > 0 else 1.0)

        if len(chart.series) >= 2 or chart.highlights:
            legend = axes.legend(
                loc="upper left",
                bbox_to_anchor=(1.01, 1.0),
                frameon=False,
                fontsize=9,
                labelcolor=INK_SECONDARY,
                handlelength=1.6,
            )
            if chart.other_series_count:
                legend.set_title(
                    f"top {len(chart.series)} of "
                    f"{len(chart.series) + chart.other_series_count}",
                    prop={"size": 9},
                )
                legend.get_title().set_color(INK_MUTED)

    def _draw_titles(self, figure: Any, axes: Any, chart: ChartData) -> None:
        axes.set_title(
            chart.title,
            color=INK_PRIMARY,
            fontsize=15,
            fontweight="semibold",
            loc="left",
            pad=26,
        )
        if chart.subtitle:
            axes.annotate(
                chart.subtitle,
                xy=(0, 1),
                xytext=(0, 12),
                xycoords="axes fraction",
                textcoords="offset points",
                color=INK_MUTED,
                fontsize=10,
                va="bottom",
                ha="left",
            )


RENDERERS: dict[str, Any] = {
    MatplotlibPngRenderer.name: MatplotlibPngRenderer,
}


class UnknownRenderer(ValueError):
    """Raised when ``INCENTIVES_RENDERER`` names a renderer that does not exist."""


def get_renderer(name: str | None = None) -> ChartRenderer:
    """Resolve a renderer by name, defaulting to ``settings.INCENTIVES_RENDERER``."""
    key = (name or settings.INCENTIVES_RENDERER).strip().lower()
    try:
        factory = RENDERERS[key]
    except KeyError:
        raise UnknownRenderer(
            f"Unknown renderer {key!r}. Available: {', '.join(sorted(RENDERERS))}."
        ) from None
    return factory()
