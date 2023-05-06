"""
Data sources — the seam between this app and wherever incentive data comes from.

A source is anything that can turn a subnet id into a :class:`SubnetIncentives`.
Two ship in the box:

``graphql``
    the real upstream Bittensor-style GraphQL API, with a connect/read timeout
    and bounded retry-with-backoff on transient failures;
``sample``
    a deterministic generator used for demos, Docker's first boot, screenshots
    and tests, so the app is never empty and never needs the network.

Adding a third (a CSV export, a second vendor, a local cache warmer) means
writing one class with a ``fetch`` method and registering it in ``SOURCES``.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import random
from typing import Any, Protocol, runtime_checkable

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from incentives.domain import IncentivePoint, IncentiveSeries, SubnetIncentives
from incentives.parsing import build_incentives_query, parse_response

logger = logging.getLogger(__name__)

# Statuses worth a second attempt: rate limiting and transient server errors.
RETRY_STATUSES = (429, 500, 502, 503, 504)


class UpstreamUnavailable(RuntimeError):
    """Raised when a source cannot produce data for a subnet."""


class ImproperlyConfiguredSource(ValueError):
    """Raised when ``INCENTIVES_SOURCE`` names a source that does not exist."""


@runtime_checkable
class IncentiveSource(Protocol):
    """The contract every data source implements."""

    name: str

    def fetch(self, subnet_uid: int) -> SubnetIncentives:
        """Return incentives for ``subnet_uid`` or raise ``UpstreamUnavailable``."""


class GraphQLIncentiveSource:
    """
    Queries the upstream GraphQL API over HTTP.

    A single pooled ``requests.Session`` is reused across calls, carrying an
    adapter configured for exponential backoff. Retries are deliberately bounded
    and only cover transient conditions — a malformed query should fail fast,
    not be hammered five times.
    """

    name = "graphql"

    def __init__(
        self,
        url: str | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
    ) -> None:
        self.url = url or settings.GRAPHQL_API_URL
        self.timeout = timeout if timeout is not None else settings.GRAPHQL_REQUEST_TIMEOUT
        self.max_retries = (
            max_retries if max_retries is not None else settings.GRAPHQL_MAX_RETRIES
        )
        self.backoff_factor = (
            backoff_factor if backoff_factor is not None else settings.GRAPHQL_RETRY_BACKOFF
        )
        self._session: requests.Session | None = None

    def build_session(self) -> requests.Session:
        retry = Retry(
            total=self.max_retries,
            connect=self.max_retries,
            read=self.max_retries,
            status=self.max_retries,
            backoff_factor=self.backoff_factor,
            status_forcelist=RETRY_STATUSES,
            allowed_methods=frozenset({"POST"}),
            raise_on_status=False,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        return session

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = self.build_session()
        return self._session

    def fetch(self, subnet_uid: int) -> SubnetIncentives:
        query = build_incentives_query(subnet_uid)

        try:
            response = self.session.post(
                self.url,
                json={"query": query},
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Incentives fetch from %s failed: %s", self.url, exc)
            raise UpstreamUnavailable(
                f"Could not reach the incentives API at {self.url}."
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Non-JSON response from %s", self.url)
            raise UpstreamUnavailable(
                "The incentives API returned a response that was not JSON."
            ) from exc

        # A GraphQL endpoint answers 200 even for query-level failures, so the
        # errors section has to be inspected explicitly rather than swallowed.
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            logger.warning("Incentives API returned GraphQL errors: %s", errors)

        series = parse_response(payload)
        if not series:
            raise UpstreamUnavailable(
                f"The incentives API returned no usable data for subnet {subnet_uid}."
            )

        return SubnetIncentives(
            subnet_uid=int(subnet_uid),
            series=series,
            source_name=self.name,
            fetched_at=dt.datetime.now(dt.UTC),
        )


class SampleIncentiveSource:
    """
    Generates a realistic, fully deterministic subnet.

    Seeded from the subnet id, so the same subnet always produces the same
    chart. That is what makes the Docker image useful on first boot with no
    upstream credentials, and what makes the committed screenshots reproducible.
    """

    name = "sample"

    def __init__(self, uid_count: int = 24, hours: int = 48, interval_minutes: int = 60):
        self.uid_count = uid_count
        self.hours = hours
        self.interval_minutes = interval_minutes

    def fetch(self, subnet_uid: int) -> SubnetIncentives:
        rng = random.Random(int(subnet_uid) * 7919)
        steps = max(2, (self.hours * 60) // self.interval_minutes)
        # A fixed anchor keeps the axis labels identical between runs.
        end = dt.datetime(2026, 9, 22, 12, 0, 0)
        start = end - dt.timedelta(minutes=self.interval_minutes * (steps - 1))

        series: list[IncentiveSeries] = []
        for index in range(self.uid_count):
            # Ranked plateaus: a few strong miners, a long tail of weak ones.
            # Scaled so even a spiking UID stays inside [0, 1] -- incentive is a
            # normalised share, so demo data that exceeded 1.0 would be a value
            # the real network can never produce.
            base = 0.30 * math.exp(-index / 5.5) + 0.004
            drift = rng.uniform(-0.12, 0.12) * base
            noise_scale = 0.05 * base + 0.002
            spike_at = rng.randrange(steps) if rng.random() < 0.25 else None

            points = []
            for step in range(steps):
                progress = step / (steps - 1)
                value = base + drift * progress
                value += math.sin(progress * math.pi * 2 + index) * noise_scale
                value += rng.gauss(0, noise_scale)
                if step == spike_at:
                    value *= rng.uniform(1.8, 2.6)
                points.append(
                    IncentivePoint(
                        timestamp=start + dt.timedelta(minutes=self.interval_minutes * step),
                        value=round(min(1.0, max(0.0, value)), 6),
                    )
                )
            series.append(IncentiveSeries.from_points(index, points))

        series.sort(key=lambda item: (-item.peak, item.uid))
        return SubnetIncentives(
            subnet_uid=int(subnet_uid),
            series=tuple(series),
            source_name=self.name,
            fetched_at=dt.datetime.now(dt.UTC),
        )


SOURCES: dict[str, Any] = {
    GraphQLIncentiveSource.name: GraphQLIncentiveSource,
    SampleIncentiveSource.name: SampleIncentiveSource,
}


def get_source(name: str | None = None) -> IncentiveSource:
    """
    Resolve a source by name, defaulting to ``settings.INCENTIVES_SOURCE``.

    Unknown names raise rather than silently falling back, so a typo in the
    environment surfaces at boot instead of quietly serving fake data.
    """
    key = (name or settings.INCENTIVES_SOURCE).strip().lower()
    try:
        factory = SOURCES[key]
    except KeyError:
        raise ImproperlyConfiguredSource(
            f"Unknown incentives source {key!r}. Available: {', '.join(sorted(SOURCES))}."
        ) from None
    return factory()
