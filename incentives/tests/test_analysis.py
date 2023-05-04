"""Tests for the pure statistics, outlier detection and fallback narrative."""

from django.test import SimpleTestCase

from incentives.analysis import (
    describe,
    detect_anomalies,
    select_top_series,
    summarise_stats,
)
from incentives.tests.factories import make_incentives, make_series


class SelectTopSeriesTests(SimpleTestCase):
    def setUp(self):
        self.series = [
            make_series(uid, [value] * 3)
            for uid, value in ((1, 0.1), (2, 0.9), (3, 0.5), (4, 0.7))
        ]

    def test_keeps_the_highest_peaking_uids_in_rank_order(self):
        top, other = select_top_series(self.series, 2)

        self.assertEqual([item.uid for item in top], [2, 4])
        self.assertEqual(other, 2)

    def test_reports_zero_others_when_everything_fits(self):
        top, other = select_top_series(self.series, 10)

        self.assertEqual(len(top), 4)
        self.assertEqual(other, 0)

    def test_a_non_positive_limit_keeps_everything(self):
        top, other = select_top_series(self.series, 0)

        self.assertEqual(len(top), 4)
        self.assertEqual(other, 0)

    def test_handles_an_empty_subnet(self):
        self.assertEqual(select_top_series([], 5), ((), 0))


class DetectAnomaliesTests(SimpleTestCase):
    def test_flags_a_sharp_step_change(self):
        series = make_series(7, [0.10, 0.11, 0.10, 0.11, 0.10, 0.95, 0.96, 0.95])

        anomalies = detect_anomalies([series])

        self.assertTrue(anomalies)
        self.assertEqual(anomalies[0].uid, 7)
        self.assertEqual(anomalies[0].direction, "jumped")
        self.assertAlmostEqual(anomalies[0].value, 0.95)

    def test_records_the_direction_of_a_drop(self):
        series = make_series(8, [0.90, 0.91, 0.90, 0.91, 0.90, 0.05, 0.06, 0.05])

        self.assertEqual(detect_anomalies([series])[0].direction, "dropped")

    def test_a_high_but_flat_series_is_not_an_anomaly(self):
        series = make_series(9, [0.90] * 12)

        self.assertEqual(detect_anomalies([series]), ())

    def test_a_steadily_climbing_series_is_not_an_anomaly(self):
        series = make_series(10, [0.1 * step for step in range(12)])

        self.assertEqual(detect_anomalies([series]), ())

    def test_short_series_are_skipped_rather_than_producing_noise(self):
        self.assertEqual(detect_anomalies([make_series(11, [0.1, 9.0])]), ())

    def test_results_are_ordered_by_severity(self):
        mild = make_series(1, [0.10, 0.11, 0.10, 0.11, 0.10, 0.40, 0.41, 0.40])
        wild = make_series(2, [0.10, 0.11, 0.10, 0.11, 0.10, 9.00, 9.01, 9.00])

        anomalies = detect_anomalies([mild, wild])

        self.assertEqual(anomalies[0].uid, 2)
        self.assertGreaterEqual(abs(anomalies[0].z_score), abs(anomalies[-1].z_score))

    def test_threshold_is_tunable(self):
        series = make_series(3, [0.10, 0.11, 0.10, 0.11, 0.10, 0.20, 0.21, 0.20])

        self.assertGreater(
            len(detect_anomalies([series], threshold=1.0)),
            len(detect_anomalies([series], threshold=50.0)),
        )


class SummariseStatsTests(SimpleTestCase):
    def test_computes_the_headline_numbers(self):
        incentives = make_incentives(
            [
                make_series(1, [0.10, 0.20, 0.30]),
                make_series(2, [0.90, 0.80, 0.70]),
            ],
            subnet_uid=18,
        )

        stats = summarise_stats(incentives)

        self.assertEqual(stats.subnet_uid, 18)
        self.assertEqual(stats.uid_count, 2)
        self.assertEqual(stats.point_count, 6)
        self.assertEqual(stats.peak_uid, 2)
        self.assertAlmostEqual(stats.peak_value, 0.9)
        self.assertAlmostEqual(stats.mean_value, 0.5)
        self.assertEqual(stats.rising_count, 1)
        self.assertEqual(stats.falling_count, 1)
        self.assertAlmostEqual(stats.window_hours, 2.0)

    def test_handles_an_empty_subnet(self):
        stats = summarise_stats(make_incentives([]))

        self.assertEqual(stats.uid_count, 0)
        self.assertEqual(stats.peak_uid, None)
        self.assertEqual(stats.window_hours, 0.0)


class DescribeTests(SimpleTestCase):
    def _stats(self):
        return summarise_stats(
            make_incentives([make_series(1, [0.1, 0.2, 0.3]), make_series(2, [0.9, 0.8, 0.7])])
        )

    def test_mentions_the_subnet_uid_count_and_peak(self):
        stats = self._stats()

        text = describe(stats, (), [make_series(2, [0.9])], 0)

        self.assertIn("Subnet 18", text)
        self.assertIn("2 UIDs", text)
        self.assertIn("UID 2", text)

    def test_reports_omitted_uids(self):
        text = describe(self._stats(), (), [make_series(2, [0.9])], 17)

        self.assertIn("17 lower-ranked UIDs are omitted", text)

    def test_reports_flagged_step_changes(self):
        anomalies = detect_anomalies(
            [make_series(7, [0.10, 0.11, 0.10, 0.11, 0.10, 0.95, 0.96, 0.95])]
        )

        text = describe(self._stats(), anomalies, [make_series(7, [0.9])], 0)

        self.assertIn("step changes were flagged", text)
        self.assertIn("UID 7", text)

    def test_says_so_when_nothing_was_flagged(self):
        self.assertIn(
            "No step changes were flagged",
            describe(self._stats(), (), [make_series(1, [0.1])], 0),
        )

    def test_empty_subnet_gets_an_honest_sentence(self):
        stats = summarise_stats(make_incentives([]))

        self.assertEqual(
            describe(stats, (), [], 0), "No incentive data is available for this subnet."
        )
