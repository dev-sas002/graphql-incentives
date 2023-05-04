"""Tests for the pure wire-format -> domain translation layer."""

import datetime as dt

from django.test import SimpleTestCase

from incentives.parsing import (
    build_incentives_query,
    build_series,
    extract_incentive_points,
    iter_incentive_entries,
    parse_response,
    parse_timestamp,
)
from incentives.tests.factories import make_response_payload


class BuildIncentivesQueryTests(SimpleTestCase):
    def test_query_embeds_the_subnet_uid(self):
        query = build_incentives_query(18)

        self.assertIn("subnets(netUid: 18)", query)
        self.assertIn("incentive", query)
        self.assertIn("timestamp", query)

    def test_numeric_string_is_coerced(self):
        self.assertIn("subnets(netUid: 7)", build_incentives_query("7"))

    def test_non_numeric_subnet_uid_is_rejected_rather_than_interpolated(self):
        # Guards against injecting arbitrary text into the query document.
        with self.assertRaises(ValueError):
            build_incentives_query("1) { __schema { types { name } } } #")


class ParseTimestampTests(SimpleTestCase):
    def test_parses_the_upstream_colon_separated_format(self):
        self.assertEqual(
            parse_timestamp("2024-01-01:12:30:45"),
            dt.datetime(2024, 1, 1, 12, 30, 45),
        )

    def test_parses_iso_8601_with_t_separator(self):
        self.assertEqual(
            parse_timestamp("2024-01-01T12:30:45"),
            dt.datetime(2024, 1, 1, 12, 30, 45),
        )

    def test_parses_space_separated_format(self):
        self.assertEqual(
            parse_timestamp("2024-01-01 12:30:45"),
            dt.datetime(2024, 1, 1, 12, 30, 45),
        )

    def test_parses_iso_8601_with_fractional_seconds(self):
        self.assertEqual(parse_timestamp("2024-01-01T12:30:45.500000").microsecond, 500000)

    def test_rejects_unparseable_strings(self):
        with self.assertRaises(ValueError):
            parse_timestamp("not-a-timestamp")

    def test_rejects_non_string_values(self):
        for value in (None, 1234567890, ["2024-01-01:00:00:00"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timestamp(value)


class IterIncentiveEntriesTests(SimpleTestCase):
    def test_unwraps_the_nested_incentive_object(self):
        entries = iter_incentive_entries([{"incentive": {"uid": 3, "data": []}}])

        self.assertEqual(entries, [{"uid": 3, "data": []}])

    def test_unwraps_a_list_valued_incentive_field(self):
        entries = iter_incentive_entries(
            [{"incentive": [{"uid": 1, "data": []}, {"uid": 2, "data": []}]}]
        )

        self.assertEqual([entry["uid"] for entry in entries], [1, 2])

    def test_passes_through_already_flat_entries(self):
        entries = iter_incentive_entries([{"uid": 4, "data": [{"value": "1"}]}])

        self.assertEqual(entries[0]["uid"], 4)

    def test_accepts_a_dict_valued_uids_section(self):
        entries = iter_incentive_entries({"incentive": {"uid": 5, "data": []}})

        self.assertEqual(entries, [{"uid": 5, "data": []}])

    def test_ignores_non_dict_members(self):
        self.assertEqual(iter_incentive_entries(["junk", None, 7]), [])

    def test_handles_missing_sections(self):
        self.assertEqual(iter_incentive_entries(None), [])
        self.assertEqual(iter_incentive_entries([]), [])


class ExtractIncentivePointsTests(SimpleTestCase):
    def test_parses_the_shape_the_query_document_implies(self):
        payload = make_response_payload(
            [(1, [("10.5", "2024-01-01:12:00:00"), ("15.0", "2024-01-01:13:00:00")])],
            nested=True,
        )

        points = extract_incentive_points(payload)

        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["uid"], 1)
        self.assertEqual(points[0]["value"], 10.5)

    def test_parses_the_flattened_shape(self):
        payload = make_response_payload([(1, [("10.5", "2024-01-01:12:00:00")])], nested=False)

        points = extract_incentive_points(payload)

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["uid"], 1)

    def test_coerces_string_uids_because_graphql_ids_are_strings(self):
        payload = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "incentive": {
                                    "uid": "7",
                                    "data": [
                                        {"value": "1.0", "timestamp": "2024-01-01:00:00:00"}
                                    ],
                                }
                            }
                        ]
                    }
                ]
            }
        }

        points = extract_incentive_points(payload)

        self.assertEqual(points[0]["uid"], 7)
        self.assertIsInstance(points[0]["uid"], int)

    def test_skips_entries_whose_uid_is_not_a_number(self):
        payload = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "incentive": {
                                    "uid": "hotkey-abc",
                                    "data": [
                                        {"value": "1.0", "timestamp": "2024-01-01:00:00:00"}
                                    ],
                                }
                            }
                        ]
                    }
                ]
            }
        }

        self.assertEqual(extract_incentive_points(payload), [])

    def test_collects_points_across_multiple_uids_and_subnets(self):
        payload = {
            "data": {
                "subnets": [
                    make_response_payload(
                        [
                            (1, [("1.0", "2024-01-01:00:00:00")]),
                            (2, [("2.0", "2024-01-01:00:00:00")]),
                        ]
                    )["data"]["subnets"][0],
                    make_response_payload([(3, [("3.0", "2024-01-01:00:00:00")])])["data"][
                        "subnets"
                    ][0],
                ]
            }
        }

        points = extract_incentive_points(payload)

        self.assertEqual(sorted(item["uid"] for item in points), [1, 2, 3])

    def test_skips_malformed_data_points_but_keeps_valid_ones(self):
        payload = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "incentive": {
                                    "uid": 2,
                                    "data": [
                                        {"timestamp": "2024-01-01:12:00:00"},
                                        {
                                            "value": "not-a-number",
                                            "timestamp": "2024-01-01:12:00:00",
                                        },
                                        {"value": "1.0", "timestamp": "bad"},
                                        {"value": "1.0"},
                                        "junk",
                                        {
                                            "value": "5.0",
                                            "timestamp": "2024-01-01:12:00:00",
                                        },
                                    ],
                                }
                            }
                        ]
                    }
                ]
            }
        }

        points = extract_incentive_points(payload)

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["value"], 5.0)

    def test_uid_zero_is_not_treated_as_missing(self):
        payload = make_response_payload([(0, [("1.0", "2024-01-01:00:00:00")])])

        self.assertEqual([item["uid"] for item in extract_incentive_points(payload)], [0])

    def test_entries_without_a_uid_are_skipped(self):
        payload = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "incentive": {
                                    "data": [
                                        {
                                            "value": "1.0",
                                            "timestamp": "2024-01-01:00:00:00",
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        }

        self.assertEqual(extract_incentive_points(payload), [])

    def test_accepts_a_single_data_point_object(self):
        payload = {
            "data": {
                "subnets": {
                    "uids": {
                        "incentive": {
                            "uid": 6,
                            "data": {
                                "value": "2.5",
                                "timestamp": "2024-01-01:00:00:00",
                            },
                        }
                    }
                }
            }
        }

        points = extract_incentive_points(payload)

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["value"], 2.5)

    def test_returns_empty_list_for_empty_or_error_payloads(self):
        for payload in (
            {},
            {"data": None},
            {"data": {}},
            {"data": {"subnets": None}},
            {"data": {"subnets": []}},
            {"errors": [{"message": "boom"}], "data": None},
            {"data": "unexpected"},
            None,
            "not-a-dict",
        ):
            with self.subTest(payload=payload):
                self.assertEqual(extract_incentive_points(payload), [])

    def test_ignores_non_dict_subnets(self):
        self.assertEqual(extract_incentive_points({"data": {"subnets": ["x", None]}}), [])


class BuildSeriesTests(SimpleTestCase):
    def test_groups_points_by_uid_and_orders_them_in_time(self):
        payload = make_response_payload(
            [
                (
                    1,
                    [
                        ("0.2", "2024-01-01:13:00:00"),
                        ("0.1", "2024-01-01:12:00:00"),
                    ],
                ),
                (2, [("0.9", "2024-01-01:12:00:00")]),
            ]
        )

        series = build_series(extract_incentive_points(payload))

        self.assertEqual([item.uid for item in series], [2, 1])  # ranked by peak
        uid_one = next(item for item in series if item.uid == 1)
        self.assertEqual(uid_one.values, (0.1, 0.2))
        self.assertEqual(uid_one.change, 0.1)

    def test_parse_response_is_the_composed_pipeline(self):
        payload = make_response_payload([(1, [("0.4", "2024-01-01:00:00:00")])])

        self.assertEqual(
            parse_response(payload), build_series(extract_incentive_points(payload))
        )

    def test_empty_input_produces_no_series(self):
        self.assertEqual(build_series([]), ())
