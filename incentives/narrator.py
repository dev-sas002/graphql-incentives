"""
Optional LLM narration of a chart.

The app is fully functional without it. ``incentives.analysis.describe`` always
produces a correct, deterministic paragraph from the computed statistics; this
module only rewrites that paragraph into something a human would rather read,
and only when an API key is configured. Every failure path — no key, package
not installed, API error, empty response — falls back to the deterministic text
and logs at most a warning.

Note the direction of the dependency: the model is given the numbers this app
already computed and is asked to phrase them. It is never asked to compute
anything, so it cannot invent a statistic the chart does not support.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from django.conf import settings

from incentives.domain import Anomaly, SubnetStats

logger = logging.getLogger(__name__)

SOURCE_RULES = "rules"
SOURCE_MODEL = "model"

SYSTEM_PROMPT = (
    "You are an analyst writing a caption for a chart of Bittensor subnet "
    "incentive values over time. You will be given the statistics that were "
    "computed from the chart's own data. Write two or three short sentences "
    "for an engineer reading the chart. Use only the numbers you are given; "
    "never invent, extrapolate or speculate about causes. Plain prose, no "
    "markdown, no bullet points, no preamble."
)


def _build_prompt(stats: SubnetStats, anomalies: Sequence[Anomaly], deterministic: str) -> str:
    lines = [
        f"subnet: {stats.subnet_uid}",
        f"uids_tracked: {stats.uid_count}",
        f"observations: {stats.point_count}",
        f"window_hours: {stats.window_hours:.1f}",
        f"peak_uid: {stats.peak_uid}",
        f"peak_value: {stats.peak_value:.6f}",
        f"mean_value: {stats.mean_value:.6f}",
        f"uids_rising: {stats.rising_count}",
        f"uids_falling: {stats.falling_count}",
        f"flagged_step_changes: {len(anomalies)}",
    ]
    for anomaly in anomalies[:5]:
        lines.append(
            f"  step_change: uid={anomaly.uid} {anomaly.direction} "
            f"{anomaly.previous_value:.6f} -> {anomaly.value:.6f} "
            f"z={anomaly.z_score}"
        )
    lines.append("")
    lines.append(f"baseline_caption: {deterministic}")
    return "\n".join(lines)


def narrate(
    stats: SubnetStats, anomalies: Sequence[Anomaly], deterministic: str
) -> tuple[str, str]:
    """
    Return ``(text, source)`` where ``source`` is ``"model"`` or ``"rules"``.

    The caller renders the source as a badge, so a reader always knows whether
    they are looking at generated prose or computed text.
    """
    if not settings.AI_SUMMARY_ENABLED or not settings.ANTHROPIC_API_KEY:
        return deterministic, SOURCE_RULES

    try:
        import anthropic
    except ImportError:
        logger.info("anthropic package not installed; using the computed summary.")
        return deterministic, SOURCE_RULES

    try:
        client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.AI_SUMMARY_TIMEOUT,
            max_retries=1,
        )
        response = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[
                {
                    "role": "user",
                    "content": _build_prompt(stats, anomalies, deterministic),
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - narration must never break the page
        logger.warning("AI summary unavailable (%s); using the computed summary.", exc)
        return deterministic, SOURCE_RULES

    text = "".join(
        block.text for block in getattr(response, "content", []) if block.type == "text"
    ).strip()

    if not text:
        return deterministic, SOURCE_RULES
    return text, SOURCE_MODEL
