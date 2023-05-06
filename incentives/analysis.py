"""
Statistics, outlier detection and the deterministic chart narrative.

Everything here is pure and offline. The optional LLM narrator in
``incentives.narrator`` builds on these numbers; it never replaces them, so the
app says something sensible with no API key and no network.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from incentives.domain import Anomaly, IncentiveSeries, SubnetIncentives, SubnetStats

# Robust z-score threshold. 3.5 is the conventional cut-off for the
# median/MAD variant; anything above it is a genuine step change rather than
# ordinary jitter.
ANOMALY_THRESHOLD = 3.5

# 0.6745 is the 0.75 quantile of the standard normal, the constant that makes
# the median absolute deviation a consistent estimator of the standard
# deviation for normally distributed data.
MAD_SCALE = 0.6745


def select_top_series(
    series: Sequence[IncentiveSeries], limit: int
) -> tuple[tuple[IncentiveSeries, ...], int]:
    """
    Keep the ``limit`` highest-peaking UIDs and report how many were dropped.

    A subnet has up to 256 UIDs. Drawing all of them produces an unreadable
    hairball and a legend taller than the chart, so the chart shows the UIDs a
    reader can actually distinguish and states the size of the remainder.
    """
    ranked = sorted(series, key=lambda item: (-item.peak, item.uid))
    if limit <= 0 or len(ranked) <= limit:
        return tuple(ranked), 0
    return tuple(ranked[:limit]), len(ranked) - limit


def detect_anomalies(
    series: Sequence[IncentiveSeries], threshold: float = ANOMALY_THRESHOLD
) -> tuple[Anomaly, ...]:
    """
    Flag step changes using a median/MAD z-score over consecutive differences.

    Differences rather than raw values, because a UID that sits legitimately
    high all day is not an anomaly — a UID that doubles between two samples is.
    The median/MAD pair is used instead of mean/stdev because a single large
    spike inflates the standard deviation enough to hide itself.
    """
    anomalies: list[Anomaly] = []

    for item in series:
        values = item.values
        if len(values) < 5:
            continue

        deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
        median = statistics.median(deltas)
        mad = statistics.median([abs(delta - median) for delta in deltas])
        if mad == 0:
            continue

        for index, delta in enumerate(deltas, start=1):
            z_score = MAD_SCALE * (delta - median) / mad
            if abs(z_score) >= threshold:
                anomalies.append(
                    Anomaly(
                        uid=item.uid,
                        timestamp=item.points[index].timestamp,
                        value=values[index],
                        previous_value=values[index - 1],
                        z_score=round(z_score, 2),
                    )
                )

    anomalies.sort(key=lambda item: -abs(item.z_score))
    return tuple(anomalies)


def summarise_stats(incentives: SubnetIncentives) -> SubnetStats:
    """Reduce a subnet to the handful of numbers shown as stat tiles."""
    window = incentives.window
    all_values = [point.value for s in incentives.series for point in s.points]
    peak_series = max(incentives.series, key=lambda item: item.peak, default=None)

    return SubnetStats(
        subnet_uid=incentives.subnet_uid,
        uid_count=len(incentives.series),
        point_count=incentives.point_count,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        peak_uid=peak_series.uid if peak_series else None,
        peak_value=peak_series.peak if peak_series else 0.0,
        mean_value=(sum(all_values) / len(all_values)) if all_values else 0.0,
        rising_count=sum(1 for item in incentives.series if item.change > 0),
        falling_count=sum(1 for item in incentives.series if item.change < 0),
    )


def describe(
    stats: SubnetStats,
    anomalies: Sequence[Anomaly],
    plotted: Sequence[IncentiveSeries],
    other_count: int,
) -> str:
    """
    Build the fallback narrative from the numbers alone.

    This is what a reader sees when no AI key is configured — so it has to be a
    genuinely useful paragraph, not a placeholder.
    """
    if not stats.uid_count:
        return "No incentive data is available for this subnet."

    sentences = [
        f"Subnet {stats.subnet_uid} reported {stats.point_count:,} incentive "
        f"observations across {stats.uid_count} UIDs over "
        f"{stats.window_hours:.0f} hours."
    ]

    if stats.peak_uid is not None:
        sentences.append(
            f"UID {stats.peak_uid} set the highest incentive at "
            f"{stats.peak_value:.4f}, against a subnet-wide mean of "
            f"{stats.mean_value:.4f}."
        )

    if stats.rising_count or stats.falling_count:
        sentences.append(
            f"{stats.rising_count} UIDs finished the window higher than they "
            f"started and {stats.falling_count} finished lower."
        )

    if other_count:
        shown = ", ".join(str(item.uid) for item in plotted[:3])
        sentences.append(
            f"The chart plots the {len(plotted)} highest-peaking UIDs "
            f"({shown}, …); {other_count} lower-ranked UIDs are omitted."
        )

    if anomalies:
        worst = anomalies[0]
        sentences.append(
            f"{len(anomalies)} step changes were flagged; the largest is UID "
            f"{worst.uid}, which {worst.direction} from {worst.previous_value:.4f} "
            f"to {worst.value:.4f} (z={worst.z_score})."
        )
    else:
        sentences.append("No step changes were flagged in this window.")

    return " ".join(sentences)
