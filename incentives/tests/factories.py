"""Shared builders for the test suite."""

from __future__ import annotations

import datetime as dt

from incentives.domain import IncentivePoint, IncentiveSeries, SubnetIncentives


def make_response_payload(uids, *, nested=True):
    """
    Build a GraphQL response document from ``[(uid, [(value, timestamp), ...])]``.

    ``nested=True`` mirrors the shape implied by the query document itself
    (``uids { incentive { uid data } }``); ``nested=False`` produces the
    flattened shape some responses use.
    """
    entries = []
    for uid, points in uids:
        incentive = {
            "uid": uid,
            "data": [
                {
                    "value": value,
                    "timestamp": timestamp,
                    "blockNumber": 1,
                    "valueBlockNumber": 1,
                }
                for value, timestamp in points
            ],
        }
        entries.append({"incentive": incentive} if nested else incentive)

    return {"data": {"subnets": [{"uids": entries}]}}


def make_series(uid: int, values, *, start=None, step_minutes=60) -> IncentiveSeries:
    start = start or dt.datetime(2024, 1, 1, 0, 0, 0)
    points = [
        IncentivePoint(
            timestamp=start + dt.timedelta(minutes=step_minutes * index),
            value=float(value),
        )
        for index, value in enumerate(values)
    ]
    return IncentiveSeries.from_points(uid, points)


def make_incentives(series, *, subnet_uid=18, source_name="test") -> SubnetIncentives:
    return SubnetIncentives(
        subnet_uid=subnet_uid,
        series=tuple(series),
        source_name=source_name,
        fetched_at=dt.datetime(2024, 1, 2, 0, 0, 0, tzinfo=dt.UTC),
    )
