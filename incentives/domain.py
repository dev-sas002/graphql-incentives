"""
Domain types for subnet incentive data.

These are plain, immutable value objects with no knowledge of HTTP, Django or
matplotlib. Every other layer (sources, analysis, renderers, views) speaks in
terms of the types defined here, which is what keeps the dependency arrows
pointing inward towards this module.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class IncentivePoint:
    """A single observation of one UID's incentive at one point in time."""

    timestamp: dt.datetime
    value: float


@dataclass(frozen=True)
class IncentiveSeries:
    """
    One UID's incentive over time, ordered oldest-first.

    Construct through :meth:`from_points` so the ordering invariant holds.
    """

    uid: int
    points: tuple[IncentivePoint, ...]

    @classmethod
    def from_points(cls, uid: int, points: Sequence[IncentivePoint]) -> IncentiveSeries:
        return cls(uid=uid, points=tuple(sorted(points, key=lambda p: p.timestamp)))

    @property
    def values(self) -> tuple[float, ...]:
        return tuple(point.value for point in self.points)

    @property
    def timestamps(self) -> tuple[dt.datetime, ...]:
        return tuple(point.timestamp for point in self.points)

    @property
    def peak(self) -> float:
        return max(self.values, default=0.0)

    @property
    def mean(self) -> float:
        values = self.values
        return sum(values) / len(values) if values else 0.0

    @property
    def latest(self) -> float:
        return self.points[-1].value if self.points else 0.0

    @property
    def first(self) -> float:
        return self.points[0].value if self.points else 0.0

    @property
    def change(self) -> float:
        """Absolute change between the first and last observation."""
        return self.latest - self.first


@dataclass(frozen=True)
class SubnetIncentives:
    """Everything a source returns for one subnet: the series plus provenance."""

    subnet_uid: int
    series: tuple[IncentiveSeries, ...]
    source_name: str
    fetched_at: dt.datetime

    @property
    def point_count(self) -> int:
        return sum(len(series.points) for series in self.series)

    @property
    def window(self) -> tuple[dt.datetime, dt.datetime] | None:
        stamps = [point.timestamp for series in self.series for point in series.points]
        if not stamps:
            return None
        return (min(stamps), max(stamps))


@dataclass(frozen=True)
class Anomaly:
    """A single observation that sits far outside its own series' behaviour."""

    uid: int
    timestamp: dt.datetime
    value: float
    previous_value: float
    z_score: float

    @property
    def direction(self) -> str:
        return "jumped" if self.value >= self.previous_value else "dropped"


@dataclass(frozen=True)
class ChartData:
    """
    The exact, renderer-agnostic description of what should be drawn.

    A renderer receives this and nothing else, so adding a new output format
    never requires touching fetching, parsing or analysis.
    """

    title: str
    subtitle: str
    x_label: str
    y_label: str
    series: tuple[IncentiveSeries, ...]
    other_series_count: int = 0
    highlights: tuple[Anomaly, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SubnetStats:
    """Headline numbers shown as stat tiles above the chart."""

    subnet_uid: int
    uid_count: int
    point_count: int
    window_start: dt.datetime | None
    window_end: dt.datetime | None
    peak_uid: int | None
    peak_value: float
    mean_value: float
    rising_count: int
    falling_count: int

    @property
    def window_hours(self) -> float:
        if not (self.window_start and self.window_end):
            return 0.0
        return (self.window_end - self.window_start).total_seconds() / 3600.0


@dataclass(frozen=True)
class ChartReport:
    """The cached unit of work: one rendered chart plus everything around it."""

    subnet_uid: int
    source_name: str
    generated_at: dt.datetime
    image_base64: str
    content_type: str
    stats: SubnetStats
    plotted_uids: tuple[int, ...]
    other_series_count: int
    anomalies: tuple[Anomaly, ...]
    summary: str
    summary_source: str
