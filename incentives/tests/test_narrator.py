"""
Tests for the optional AI summary.

No test makes a real API call: the anthropic client is always patched, and the
default configuration (no key) is exercised as the primary path.
"""

import sys
import types
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from incentives.analysis import summarise_stats
from incentives.narrator import SOURCE_MODEL, SOURCE_RULES, narrate
from incentives.tests.factories import make_incentives, make_series

BASELINE = "Subnet 18 reported 6 observations."


def make_stats():
    return summarise_stats(
        make_incentives([make_series(1, [0.1, 0.2, 0.3]), make_series(2, [0.9, 0.8, 0.7])])
    )


def fake_anthropic_module(response=None, error=None):
    """A stand-in for the real ``anthropic`` package."""
    module = types.ModuleType("anthropic")
    client = MagicMock()
    if error is not None:
        client.messages.create.side_effect = error
    else:
        client.messages.create.return_value = response
    module.Anthropic = MagicMock(return_value=client)
    module._client = client
    return module


def text_response(text):
    block = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(content=[block])


class NarratorTests(SimpleTestCase):
    @override_settings(AI_SUMMARY_ENABLED=True, ANTHROPIC_API_KEY="")
    def test_without_a_key_the_computed_summary_is_used(self):
        self.assertEqual(narrate(make_stats(), (), BASELINE), (BASELINE, SOURCE_RULES))

    @override_settings(AI_SUMMARY_ENABLED=False, ANTHROPIC_API_KEY="sk-test")
    def test_disabling_the_feature_short_circuits_before_any_import(self):
        self.assertEqual(narrate(make_stats(), (), BASELINE), (BASELINE, SOURCE_RULES))

    @override_settings(
        AI_SUMMARY_ENABLED=True,
        ANTHROPIC_API_KEY="sk-test",
        ANTHROPIC_MODEL="claude-opus-5",
        AI_SUMMARY_TIMEOUT=5.0,
    )
    def test_uses_the_model_response_when_one_is_returned(self):
        module = fake_anthropic_module(text_response("  Subnet 18 looks healthy.  "))

        with patch.dict(sys.modules, {"anthropic": module}):
            text, source = narrate(make_stats(), (), BASELINE)

        self.assertEqual(text, "Subnet 18 looks healthy.")
        self.assertEqual(source, SOURCE_MODEL)

        kwargs = module._client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5")
        self.assertIn("peak_uid", kwargs["messages"][0]["content"])
        self.assertIn(BASELINE, kwargs["messages"][0]["content"])
        module.Anthropic.assert_called_once_with(api_key="sk-test", timeout=5.0, max_retries=1)

    @override_settings(AI_SUMMARY_ENABLED=True, ANTHROPIC_API_KEY="sk-test")
    def test_api_errors_fall_back_without_raising(self):
        module = fake_anthropic_module(error=RuntimeError("503 overloaded"))

        with (
            patch.dict(sys.modules, {"anthropic": module}),
            self.assertLogs("incentives.narrator", level="WARNING"),
        ):
            self.assertEqual(narrate(make_stats(), (), BASELINE), (BASELINE, SOURCE_RULES))

    @override_settings(AI_SUMMARY_ENABLED=True, ANTHROPIC_API_KEY="sk-test")
    def test_an_empty_response_falls_back(self):
        module = fake_anthropic_module(text_response("   "))

        with patch.dict(sys.modules, {"anthropic": module}):
            self.assertEqual(narrate(make_stats(), (), BASELINE), (BASELINE, SOURCE_RULES))

    @override_settings(AI_SUMMARY_ENABLED=True, ANTHROPIC_API_KEY="sk-test")
    def test_a_missing_package_falls_back(self):
        real_import = __import__

        def fail_on_anthropic(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {}, clear=False):
            sys.modules.pop("anthropic", None)
            with patch("builtins.__import__", side_effect=fail_on_anthropic):
                self.assertEqual(narrate(make_stats(), (), BASELINE), (BASELINE, SOURCE_RULES))
