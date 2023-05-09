"""Tests for the data-source seam. No test performs real network I/O."""

from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from incentives.sources import (
    RETRY_STATUSES,
    GraphQLIncentiveSource,
    ImproperlyConfiguredSource,
    IncentiveSource,
    SampleIncentiveSource,
    UpstreamUnavailable,
    get_source,
)
from incentives.tests.factories import make_response_payload

PAYLOAD = make_response_payload(
    [
        (1, [("0.1", "2024-01-01:12:00:00"), ("0.2", "2024-01-01:13:00:00")]),
        (2, [("0.3", "2024-01-01:12:00:00")]),
    ]
)


@override_settings(
    GRAPHQL_API_URL="https://example.test/graphql",
    GRAPHQL_REQUEST_TIMEOUT=3.0,
    GRAPHQL_MAX_RETRIES=2,
    GRAPHQL_RETRY_BACKOFF=0.1,
)
class GraphQLIncentiveSourceTests(SimpleTestCase):
    def test_returns_series_for_a_well_formed_response(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.return_value = PAYLOAD
            incentives = source.fetch(18)

        self.assertEqual(incentives.subnet_uid, 18)
        self.assertEqual(incentives.source_name, "graphql")
        self.assertEqual(sorted(item.uid for item in incentives.series), [1, 2])
        self.assertEqual(incentives.point_count, 3)

    def test_posts_the_configured_url_query_and_timeout(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.return_value = PAYLOAD
            source.fetch(18)

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://example.test/graphql")
        self.assertIn("subnets(netUid: 18)", kwargs["json"]["query"])
        self.assertEqual(kwargs["timeout"], 3.0)

    def test_subnet_uid_drives_the_query(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.return_value = PAYLOAD
            source.fetch(42)

        self.assertIn("subnets(netUid: 42)", mock_post.call_args.kwargs["json"]["query"])

    def test_retry_policy_covers_transient_failures_only(self):
        adapter = GraphQLIncentiveSource().session.get_adapter("https://example.test/")
        retry = adapter.max_retries

        self.assertEqual(retry.total, 2)
        self.assertEqual(retry.backoff_factor, 0.1)
        self.assertEqual(tuple(retry.status_forcelist), RETRY_STATUSES)
        self.assertIn("POST", retry.allowed_methods)

    def test_session_is_reused_between_fetches(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.return_value = PAYLOAD
            source.fetch(18)
            source.fetch(18)

        self.assertEqual(mock_post.call_count, 2)

    def test_connection_failure_raises_upstream_unavailable(self):
        source = GraphQLIncentiveSource()

        with (
            patch.object(source.session, "post", side_effect=requests.ConnectionError("down")),
            self.assertRaises(UpstreamUnavailable),
        ):
            source.fetch(18)

    def test_timeout_raises_upstream_unavailable(self):
        source = GraphQLIncentiveSource()

        with (
            patch.object(source.session, "post", side_effect=requests.Timeout("slow")),
            self.assertRaises(UpstreamUnavailable),
        ):
            source.fetch(18)

    def test_http_error_status_raises_upstream_unavailable(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = requests.HTTPError("500")
            with self.assertRaises(UpstreamUnavailable):
                source.fetch(18)

    def test_non_json_body_raises_upstream_unavailable(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.side_effect = ValueError("not json")
            with self.assertRaises(UpstreamUnavailable):
                source.fetch(18)

    def test_empty_payload_raises_upstream_unavailable(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.return_value = {"data": {"subnets": []}}
            with self.assertRaises(UpstreamUnavailable):
                source.fetch(18)

    def test_graphql_level_errors_are_logged(self):
        source = GraphQLIncentiveSource()

        with patch.object(source.session, "post") as mock_post:
            mock_post.return_value.json.return_value = {
                "data": None,
                "errors": [{"message": "subnet not found"}],
            }
            with (
                self.assertLogs("incentives.sources", level="WARNING") as logs,
                self.assertRaises(UpstreamUnavailable),
            ):
                source.fetch(18)

        self.assertTrue(any("subnet not found" in line for line in logs.output))


class SampleIncentiveSourceTests(SimpleTestCase):
    def test_generates_a_populated_subnet(self):
        incentives = SampleIncentiveSource().fetch(18)

        self.assertEqual(incentives.source_name, "sample")
        self.assertEqual(len(incentives.series), 24)
        self.assertGreater(incentives.point_count, 100)

    def test_is_deterministic_for_a_given_subnet(self):
        first = SampleIncentiveSource().fetch(18)
        second = SampleIncentiveSource().fetch(18)

        self.assertEqual(first.series, second.series)

    def test_different_subnets_produce_different_data(self):
        self.assertNotEqual(
            SampleIncentiveSource().fetch(18).series,
            SampleIncentiveSource().fetch(21).series,
        )

    def test_values_are_never_negative(self):
        incentives = SampleIncentiveSource().fetch(5)

        self.assertTrue(
            all(point.value >= 0 for item in incentives.series for point in item.points)
        )

    def test_values_stay_inside_the_normalised_range(self):
        incentives = SampleIncentiveSource().fetch(18)

        values = [point.value for item in incentives.series for point in item.points]

        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))

    def test_series_are_ranked_by_peak(self):
        peaks = [item.peak for item in SampleIncentiveSource().fetch(18).series]

        self.assertEqual(peaks, sorted(peaks, reverse=True))


class SourceRegistryTests(SimpleTestCase):
    @override_settings(INCENTIVES_SOURCE="sample")
    def test_resolves_the_configured_source(self):
        self.assertIsInstance(get_source(), SampleIncentiveSource)

    @override_settings(INCENTIVES_SOURCE="graphql")
    def test_explicit_name_overrides_the_setting(self):
        self.assertIsInstance(get_source("sample"), SampleIncentiveSource)

    @override_settings(INCENTIVES_SOURCE="graphql")
    def test_default_is_the_real_upstream(self):
        self.assertIsInstance(get_source(), GraphQLIncentiveSource)

    def test_unknown_source_fails_loudly(self):
        with self.assertRaises(ImproperlyConfiguredSource):
            get_source("does-not-exist")

    def test_both_shipped_sources_satisfy_the_protocol(self):
        for source in (GraphQLIncentiveSource(), SampleIncentiveSource()):
            with self.subTest(source=source.name):
                self.assertIsInstance(source, IncentiveSource)
