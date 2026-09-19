import builtins
import unittest
from unittest.mock import mock_open, patch

import system_metrics


class SystemMetricsTests(unittest.TestCase):
    def test_cpu_percent_calculates_usage_between_two_proc_stat_samples(self):
        original_previous = system_metrics._previous_cpu_times
        first_sample = "cpu  40 0 0 0 0 0 0 0 0 0\n"
        second_sample = "cpu  100 0 0 20 0 0 0 0 0 0\n"

        try:
            system_metrics._previous_cpu_times = None
            with patch.object(builtins, "open", mock_open(read_data=first_sample)):
                self.assertEqual(system_metrics.cpu_percent(), 0.0)
            with patch.object(builtins, "open", mock_open(read_data=second_sample)):
                self.assertEqual(system_metrics.cpu_percent(), 75.0)
        finally:
            system_metrics._previous_cpu_times = original_previous
