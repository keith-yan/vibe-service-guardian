import unittest

import json

from vsg.telemetry import parse_amd_smi_json, parse_nvidia_telemetry, parse_windows_gpu_counters


class TelemetryTests(unittest.TestCase):
    def test_nvidia_live_parser_preserves_unsupported_sensors_as_null(self):
        sample = "0, NVIDIA RTX 4090, 73, 44, 24564, 12000, 12564, 69, N/A, 311.5, 450\n"
        item = parse_nvidia_telemetry(sample)[0]
        self.assertEqual(item["gpu_util_percent"], 73)
        self.assertAlmostEqual(item["memory_used_gib"], 11.72, places=2)
        self.assertIsNone(item["fan_percent"])
        self.assertEqual(item["telemetry_status"], "measured")

    def test_windows_adapter_counters_are_sorted_by_measured_allocation(self):
        sample = json.dumps(
            [
                {"Adapter": "b", "DedicatedBytes": 1024**3, "SharedBytes": 0, "UtilizationPercent": 10},
                {"Adapter": "a", "DedicatedBytes": 4 * 1024**3, "SharedBytes": 512 * 1024**2, "UtilizationPercent": 80},
            ]
        )
        items = parse_windows_gpu_counters(sample)
        self.assertEqual(items[0]["adapter"], "a")
        self.assertEqual(items[0]["dedicated_used_gib"], 4)
        self.assertEqual(items[0]["gpu_util_percent"], 80)

    def test_amd_smi_json_parser_handles_nested_metrics_and_units(self):
        sample = json.dumps(
            {
                "gpu_data": [
                    {
                        "gpu": 0,
                        "asic": {"market_name": "AMD Instinct Future"},
                        "usage": {"gfx_activity": "73 %"},
                        "vram": {"total_vram": "24576 MB", "used_vram": "12288 MB"},
                        "temperature": {"temperature_edge": "67 C"},
                        "power": {"current_socket_power": "252 W"},
                        "fan": {"fan_speed": "41 %"},
                    }
                ]
            }
        )
        item = parse_amd_smi_json(sample)[0]
        self.assertEqual(item["vendor"], "AMD")
        self.assertEqual(item["name"], "AMD Instinct Future")
        self.assertEqual(item["gpu_util_percent"], 73)
        self.assertEqual(item["memory_total_gib"], 24)
        self.assertEqual(item["memory_used_gib"], 12)
        self.assertEqual(item["power_w"], 252)


if __name__ == "__main__":
    unittest.main()
