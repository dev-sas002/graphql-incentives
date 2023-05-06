"""
The application service: fetch -> analyse -> render -> cache.

Views call exactly one function here and do no work of their own. The pipeline
is deliberately explicit because each stage has a different cost and a different
failure mode:

* **fetch** is a network call that can be slow or down;
* **analyse** is cheap and pure;
* **render** is CPU-bound matplotlib work that blocks a worker thread.

The rendered report is cached as a whole. Before caching, every request to
``/plot/`` performed a live upstream fetch *and* a full matplotlib render inside
the request/response cycle — roughly 200-400 ms of CPU per hit even when the
upstream data had not changed, which put a hard ceiling of a few requests per
second per worker on the app. Incentive data updates on the order of minutes,
so a short TTL removes essentially all of that work while keeping the chart
fresh enough to be useful.
"""

from __future__ import annotations

import base64
import datetime as dt
import logging

from django.conf import settings
from django.core.cache import cache

from incentives.analysis import describe, detect_anomalies, select_top_series, summarise_stats
from incentives.domain import ChartData, ChartReport
from incentives.narrator import narrate
from incentives.renderers import ChartRenderer, get_renderer
from incentives.sources import IncentiveSource, UpstreamUnavailable, get_source

logger = logging.getLogger(__name__)

CACHE_VERSION = 2


def cache_key(subnet_uid: int, top_n: int, source_name: str, renderer_name: str) -> str:
    """
    Namespaced, fully-qualified cache key.

    Every input that changes the output is in the key — including the source and
    renderer names, so flipping ``INCENTIVES_SOURCE`` from ``sample`` to
    ``graphql`` cannot serve a stale sample chart.
    """
    return f"incentives:v{CACHE_VERSION}:{source_name}:{renderer_name}:{subnet_uid}:{top_n}"


def build_report(
    subnet_uid: int,
    top_n: int,
    *,
    source: IncentiveSource | None = None,
    renderer: ChartRenderer | None = None,
) -> ChartReport:
    """Run the whole pipeline once, without touching the cache."""
    source = source or get_source()
    renderer = renderer or get_renderer()

    incentives = source.fetch(subnet_uid)
    stats = summarise_stats(incentives)
    anomalies = detect_anomalies(incentives.series)
    plotted, other_count = select_top_series(incentives.series, top_n)

    deterministic = describe(stats, anomalies, plotted, other_count)
    summary, summary_source = narrate(stats, anomalies, deterministic)

    window = incentives.window
    if window:
        subtitle = (
            f"{window[0]:%d %b %H:%M} – {window[1]:%d %b %H:%M} UTC · "
            f"{stats.point_count:,} observations"
        )
    else:
        subtitle = f"{stats.point_count:,} observations"

    chart = ChartData(
        title=f"Incentive over time · subnet {subnet_uid}",
        subtitle=subtitle,
        x_label="Time (UTC)",
        y_label="Incentive",
        series=plotted,
        other_series_count=other_count,
        highlights=anomalies,
    )

    image = renderer.render(chart)

    return ChartReport(
        subnet_uid=int(subnet_uid),
        source_name=incentives.source_name,
        generated_at=dt.datetime.now(dt.UTC),
        image_base64=base64.b64encode(image).decode("ascii"),
        content_type=renderer.content_type,
        stats=stats,
        plotted_uids=tuple(item.uid for item in plotted),
        other_series_count=other_count,
        anomalies=anomalies,
        summary=summary,
        summary_source=summary_source,
    )


def get_report(subnet_uid: int, top_n: int | None = None) -> tuple[ChartReport, bool]:
    """
    Return ``(report, was_cached)``, rendering only on a miss.

    Raises :class:`~incentives.sources.UpstreamUnavailable` when the source
    cannot produce data; callers turn that into a 503 page.
    """
    top_n = settings.INCENTIVES_TOP_N if top_n is None else top_n
    source = get_source()
    renderer = get_renderer()
    key = cache_key(subnet_uid, top_n, source.name, renderer.name)

    cached = cache.get(key)
    if cached is not None:
        return cached, True

    report = build_report(subnet_uid, top_n, source=source, renderer=renderer)
    cache.set(key, report, settings.INCENTIVES_CACHE_TTL)
    return report, False


def invalidate(subnet_uid: int, top_n: int | None = None) -> None:
    """Drop one cached report — used by the ``refresh`` control on the page."""
    top_n = settings.INCENTIVES_TOP_N if top_n is None else top_n
    cache.delete(cache_key(subnet_uid, top_n, get_source().name, get_renderer().name))


__all__ = [
    "UpstreamUnavailable",
    "build_report",
    "cache_key",
    "get_report",
    "invalidate",
]
