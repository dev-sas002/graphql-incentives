from django.test import Client, TestCase
from django.urls import reverse
from unittest.mock import patch


class ExtractIncentivesFromResponseTests(TestCase):
    def test_extract_incentives_handles_typical_response_shape(self):
        from incentives.views import _extract_incentives_from_response

        response_data = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "uid": 1,
                                "data": [
                                    {
                                        "value": "10.5",
                                        "timestamp": "2024-01-01:12:00:00",
                                    },
                                    {
                                        "value": "15.0",
                                        "timestamp": "2024-01-01:13:00:00",
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        }

        incentives = _extract_incentives_from_response(response_data)

        self.assertEqual(len(incentives), 2)
        self.assertEqual(incentives[0]["uid"], 1)
        self.assertEqual(incentives[0]["value"], 10.5)
        self.assertLess(incentives[0]["timestamp"], incentives[1]["timestamp"])

    def test_extract_incentives_ignores_malformed_entries(self):
        from incentives.views import _extract_incentives_from_response

        response_data = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "uid": 2,
                                "data": [
                                    {
                                        # Missing value field
                                        "timestamp": "2024-01-01:12:00:00",
                                    },
                                    {
                                        "value": "not-a-number",
                                        "timestamp": "2024-01-01:12:00:00",
                                    },
                                    {
                                        "value": "5.0",
                                        "timestamp": "2024-01-01:12:00:00",
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        }

        incentives = _extract_incentives_from_response(response_data)

        self.assertEqual(len(incentives), 1)
        self.assertEqual(incentives[0]["uid"], 2)
        self.assertEqual(incentives[0]["value"], 5.0)


class FetchAndPlotDataTests(TestCase):
    @patch("incentives.views.requests.post")
    def test_fetch_and_plot_data_success_returns_base64_graph(self, mock_post):
        from incentives.views import fetch_and_plot_data

        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {
            "data": {
                "subnets": [
                    {
                        "uids": [
                            {
                                "uid": 1,
                                "data": [
                                    {
                                        "value": "10.5",
                                        "timestamp": "2024-01-01:12:00:00",
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        }

        graph = fetch_and_plot_data()

        self.assertIsInstance(graph, str)
        self.assertGreater(len(graph), 0)

    @patch("incentives.views.requests.post")
    def test_fetch_and_plot_data_handles_http_error(self, mock_post):
        from incentives.views import fetch_and_plot_data

        mock_post.side_effect = Exception("Network error")

        graph = fetch_and_plot_data()

        self.assertIsNone(graph)

    @patch("incentives.views.requests.post")
    def test_fetch_and_plot_data_handles_empty_incentives(self, mock_post):
        from incentives.views import fetch_and_plot_data

        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"data": {"subnets": []}}

        graph = fetch_and_plot_data()

        self.assertIsNone(graph)


class PlotViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("incentives.views.fetch_and_plot_data")
    def test_plot_view_renders_template_on_success(self, mock_fetch):
        mock_fetch.return_value = "dummy-base64"

        response = self.client.get(reverse("plot_view"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "plot.html")
        self.assertIn("graph", response.context)

    @patch("incentives.views.fetch_and_plot_data")
    def test_plot_view_returns_500_when_no_graph(self, mock_fetch):
        mock_fetch.return_value = None

        response = self.client.get(reverse("plot_view"))

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Unable to fetch incentives data", response.content)


class HomeViewTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_home_view_renders_home_template(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "home.html")
