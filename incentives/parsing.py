"""
Pure translation between the upstream GraphQL wire format and the domain model.

No network, no Django, no matplotlib: every function here is a function of its
arguments, which is why the bulk of the test suite lives against this module.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from incentives.domain import IncentivePoint, IncentiveSeries

INCENTIVES_QUERY = """
query SubnetIncentives {
  subnets(netUid: %d) {
    uids {
      incentive {
        uid
        data {
          value
          valueBlockNumber
          timestamp
          blockNumber
        }
      }
    }
  }
}
"""

# Timestamp formats accepted from the upstream API. The API historically used
# the first (colon separated) form; ISO 8601 variants are accepted as well so a
# harmless upstream format change does not silently discard every data point.
TIMESTAMP_FORMATS = (
    "%Y-%m-%d:%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def build_incentives_query(subnet_uid: int) -> str:
    """
    Build the GraphQL query document for a subnet.

    ``subnet_uid`` is coerced to ``int`` so a mis-configured or attacker-supplied
    value can never be interpolated verbatim into the document.
    """
    return INCENTIVES_QUERY % int(subnet_uid)


def parse_timestamp(value: Any) -> dt.datetime:
    """Parse an upstream timestamp, raising ``ValueError`` if no format matches."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")

    for fmt in TIMESTAMP_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue

    # Final fallback: full ISO 8601 (handles fractional seconds / offsets).
    return dt.datetime.fromisoformat(value)


def iter_incentive_entries(uids: Any) -> list[dict[str, Any]]:
    """
    Normalise the ``uids`` section of a subnet into a flat list of entries that
    each expose ``uid`` and ``data`` keys.

    The query document nests the payload as ``uids { incentive { uid data } }``
    so entries usually look like ``{"incentive": {...}}``. Older/flattened
    responses put ``uid``/``data`` directly on the entry, and some responses use
    a dict rather than a list, so all three shapes are supported.
    """
    candidates = uids.get("incentive") or [] if isinstance(uids, dict) else uids or []

    if isinstance(candidates, dict):
        candidates = [candidates]

    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        nested = candidate.get("incentive")
        if isinstance(nested, dict):
            entries.append(nested)
        elif isinstance(nested, list):
            entries.extend(item for item in nested if isinstance(item, dict))
        else:
            entries.append(candidate)

    return entries


def extract_incentive_points(response_data: Any) -> list[dict[str, Any]]:
    """
    Flatten a GraphQL response document into ``{"uid", "value", "timestamp"}``
    dicts, skipping anything malformed rather than aborting the whole request.
    """
    if not isinstance(response_data, dict):
        return []

    data_section = response_data.get("data") or {}
    if not isinstance(data_section, dict):
        return []

    subnets = data_section.get("subnets") or []
    if isinstance(subnets, dict):
        subnets = [subnets]

    points: list[dict[str, Any]] = []

    for subnet in subnets:
        if not isinstance(subnet, dict):
            continue

        for entry in iter_incentive_entries(subnet.get("uids")):
            data_points = entry.get("data") or []

            if isinstance(data_points, dict):
                data_points = [data_points]

            if not data_points:
                continue

            # GraphQL ``ID`` scalars are serialised as strings, so a uid can
            # arrive as either 3 or "3". Coercing here is what lets the rest of
            # the app rely on ``IncentiveSeries.uid`` actually being an int --
            # without it, sorting a mixed batch raises a TypeError.
            try:
                uid = int(entry["uid"])
            except (KeyError, TypeError, ValueError):
                continue

            for incentive_data in data_points:
                if not isinstance(incentive_data, dict):
                    continue
                try:
                    value = float(incentive_data["value"])
                    timestamp = parse_timestamp(incentive_data["timestamp"])
                except (KeyError, TypeError, ValueError):
                    continue

                points.append({"uid": uid, "value": value, "timestamp": timestamp})

    return points


def build_series(points: list[dict[str, Any]]) -> tuple[IncentiveSeries, ...]:
    """Group flat points into one ordered :class:`IncentiveSeries` per UID."""
    grouped: dict[int, list[IncentivePoint]] = defaultdict(list)
    for point in points:
        grouped[point["uid"]].append(
            IncentivePoint(timestamp=point["timestamp"], value=point["value"])
        )

    series = [
        IncentiveSeries.from_points(uid, uid_points) for uid, uid_points in grouped.items()
    ]
    series.sort(key=lambda item: (-item.peak, item.uid))
    return tuple(series)


def parse_response(response_data: Any) -> tuple[IncentiveSeries, ...]:
    """Convenience: response document in, ordered series out."""
    return build_series(extract_incentive_points(response_data))
