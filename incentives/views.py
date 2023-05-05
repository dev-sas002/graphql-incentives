import base64
import datetime
import io
import logging
from typing import Any, Dict, List

import matplotlib
import matplotlib.pyplot as plt
import requests
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

# Use the Agg backend for non-interactive plotting
matplotlib.use("Agg")

logger = logging.getLogger(__name__)


INCENTIVES_QUERY = """
query MyQuery {
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


def _build_incentives_query(subnet_uid: int) -> str:
    """
    Build the GraphQL query used to retrieve incentives for a given subnet.
    """
    return INCENTIVES_QUERY % subnet_uid


def _extract_incentives_from_response(response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse the incentives GraphQL response into a normalized list of points.

    This is kept separate from network and plotting concerns so that it can
    be validated in isolation and adapted if the upstream API changes shape.
    """
    data_section = response_data.get("data") or {}
    subnets = data_section.get("subnets") or []

    incentives: List[Dict[str, Any]] = []

    for subnet in subnets:
        uids = subnet.get("uids") or []

        # Support both list-based and dict-based shapes for uids.
        if isinstance(uids, dict):
            uid_entries = uids.get("incentive") or []
        else:
            uid_entries = uids

        for incentive_entry in uid_entries:
            uid = incentive_entry.get("uid")
            data_points = incentive_entry.get("data") or []

            if uid is None or not data_points:
                continue

            for incentive_data in data_points:
                try:
                    value = float(incentive_data["value"])
                    timestamp_str = incentive_data["timestamp"]
                    timestamp = datetime.datetime.strptime(
                        timestamp_str,
                        "%Y-%m-%d:%H:%M:%S",
                    )
                except (KeyError, TypeError, ValueError):
                    # Ignore malformed entries while continuing to process
                    continue

                incentives.append(
                    {
                        "uid": uid,
                        "value": value,
                        "timestamp": timestamp,
                    }
                )

    return incentives


def fetch_and_plot_data():
    """
    Fetch incentives data from the configured GraphQL API and return a base64-encoded PNG plot.

    Returns None if data cannot be fetched or plotted, allowing the caller to handle errors.
    """
    query = _build_incentives_query(settings.GRAPHQL_SUBNET_UID)

    try:
        response = requests.post(
            settings.GRAPHQL_API_URL,
            json={"query": query},
            timeout=getattr(settings, "GRAPHQL_REQUEST_TIMEOUT", 10),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(
            "Failed to fetch incentives data from %s: %s",
            settings.GRAPHQL_API_URL,
            exc,
        )
        return None

    try:
        response_data = response.json()
    except ValueError:
        logger.warning("Received non-JSON response from incentives GraphQL API")
        return None

    incentives = _extract_incentives_from_response(response_data)

    if not incentives:
        return None

    # Sort incentives by timestamp for a consistent, readable plot.
    incentives.sort(key=lambda item: item["timestamp"])

    # Plot the data
    plt.figure(figsize=(20, 10))

    incentives_by_uid: Dict[Any, List[Dict[str, Any]]] = {}
    for item in incentives:
        incentives_by_uid.setdefault(item["uid"], []).append(item)

    for uid, uid_data in incentives_by_uid.items():
        timestamps = [item["timestamp"] for item in uid_data]
        values = [item["value"] for item in uid_data]
        plt.plot(timestamps, values, label=f"UID {uid}", alpha=0.7)

    plt.xlabel("Timestamp")
    plt.ylabel("Value")
    plt.title(
        "Incentive Value Over Time for All UIDs on Subnet %d"
        % settings.GRAPHQL_SUBNET_UID
    )
    plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize="small")
    plt.grid(True)
    plt.xticks(rotation=45)

    max_value = max(item["value"] for item in incentives)
    plt.ylim(0, max_value + 50)

    plt.tight_layout()

    buffer = io.BytesIO()
    try:
        plt.savefig(buffer, format="png", bbox_inches="tight")
    finally:
        plt.close()

    buffer.seek(0)
    image_png = buffer.getvalue()
    buffer.close()

    graph = base64.b64encode(image_png)
    return graph.decode("utf-8")


def plot_view(request):
    graph = fetch_and_plot_data()
    if graph is None:
        return HttpResponse(
            "Unable to fetch incentives data at this time.",
            status=500,
        )
    return render(request, "plot.html", {"graph": graph})


def home_view(request):
    return render(request, "home.html")

