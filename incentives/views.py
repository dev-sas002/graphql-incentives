"""
HTTP layer. Views parse and validate request parameters, call one service
function and choose a template — no fetching, parsing, analysis or plotting
happens here.
"""

from __future__ import annotations

import base64
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from incentives import services
from incentives.renderers import MAX_SERIES
from incentives.sources import UpstreamUnavailable

logger = logging.getLogger(__name__)

# Bittensor subnet ids are small non-negative integers; the ceiling exists so a
# nonsense value becomes a 400 here rather than an upstream error later.
MAX_SUBNET_UID = 1023

# Subnets offered in the page's picker.
FEATURED_SUBNETS = (1, 5, 11, 18, 21, 64)


class InvalidParameter(ValueError):
    """Raised when a query parameter cannot be used."""


def _parse_subnet(request: HttpRequest) -> int:
    raw = request.GET.get("subnet")
    if raw in (None, ""):
        return settings.GRAPHQL_SUBNET_UID
    try:
        subnet = int(raw)
    except (TypeError, ValueError):
        raise InvalidParameter(f"{raw!r} is not a valid subnet id.") from None
    if not 0 <= subnet <= MAX_SUBNET_UID:
        raise InvalidParameter(f"Subnet id must be between 0 and {MAX_SUBNET_UID}.")
    return subnet


def _parse_top_n(request: HttpRequest) -> int:
    raw = request.GET.get("top")
    if raw in (None, ""):
        return settings.INCENTIVES_TOP_N
    try:
        top_n = int(raw)
    except (TypeError, ValueError):
        raise InvalidParameter(f"{raw!r} is not a valid UID count.") from None
    # The chart has a fixed eight-slot categorical palette; asking for more
    # series than there are slots would mean cycling colours.
    return max(1, min(top_n, MAX_SERIES))


def home_view(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "home.html",
        {
            "featured_subnets": FEATURED_SUBNETS,
            "default_subnet": settings.GRAPHQL_SUBNET_UID,
            "source_name": settings.INCENTIVES_SOURCE,
        },
    )


def plot_view(request: HttpRequest) -> HttpResponse:
    """Render the dashboard page for one subnet."""
    try:
        subnet_uid = _parse_subnet(request)
        top_n = _parse_top_n(request)
    except InvalidParameter as exc:
        return render(
            request,
            "error.html",
            {"message": str(exc), "subnet_uid": settings.GRAPHQL_SUBNET_UID},
            status=400,
        )

    if request.GET.get("refresh"):
        services.invalidate(subnet_uid, top_n)
        query = f"?subnet={subnet_uid}&top={top_n}"
        return redirect(f"{request.path}{query}")

    try:
        report, was_cached = services.get_report(subnet_uid, top_n)
    except UpstreamUnavailable as exc:
        return render(
            request,
            "error.html",
            {
                "message": str(exc),
                "subnet_uid": subnet_uid,
                "top_n": top_n,
                "featured_subnets": FEATURED_SUBNETS,
            },
            status=503,
        )

    return render(
        request,
        "plot.html",
        {
            "report": report,
            "stats": report.stats,
            "subnet_uid": subnet_uid,
            "top_n": top_n,
            "was_cached": was_cached,
            "cache_ttl": settings.INCENTIVES_CACHE_TTL,
            "top_choices": range(2, MAX_SERIES + 1),
            "featured_subnets": FEATURED_SUBNETS,
            "anomalies": report.anomalies[:6],
        },
    )


def chart_image_view(request: HttpRequest) -> HttpResponse:
    """
    Serve the rendered chart on its own URL.

    Splitting the image out of the HTML keeps the document small, lets the
    browser and any upstream proxy cache the expensive artefact, and lets the
    page show a skeleton while the chart loads.
    """
    try:
        subnet_uid = _parse_subnet(request)
        top_n = _parse_top_n(request)
    except InvalidParameter as exc:
        return HttpResponse(str(exc), status=400, content_type="text/plain")

    try:
        report, _ = services.get_report(subnet_uid, top_n)
    except UpstreamUnavailable as exc:
        return HttpResponse(str(exc), status=503, content_type="text/plain")

    response = HttpResponse(
        base64.b64decode(report.image_base64), content_type=report.content_type
    )
    response["Cache-Control"] = f"public, max-age={settings.INCENTIVES_CACHE_TTL}"
    return response


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe for the container healthcheck. Never touches the upstream."""
    return JsonResponse({"status": "ok", "source": settings.INCENTIVES_SOURCE})
