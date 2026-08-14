import json
import tempfile
import unittest
from pathlib import Path

from vsg.hardware import (
    parse_linux_pci_devices,
    parse_nvidia_smi,
    parse_system_profiler,
    parse_windows_video_json,
)


class HardwareTests(unittest.TestCase):
    def test_nvidia_smi_reports_measured_memory(self):
        items = parse_nvidia_smi("NVIDIA GeForce RTX 4090, 24564, 22000, 590.12, 8.9\n")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["memory_source"], "nvidia-smi")
        self.assertAlmostEqual(items[0]["memory_total_gib"], 23.99, places=2)
        self.assertEqual(items[0]["support_tier"], "supported")

    def test_windows_cim_does_not_trust_truncated_adapter_ram(self):
        raw = json.dumps(
            [
                {"Name": "AMD Radeon RX 9070 XT", "AdapterRAM": 4293918720, "DriverVersion": "1"},
                {"Name": "Remote Virtual Display Adapter", "AdapterRAM": None, "DriverVersion": "2"},
            ]
        )
        items = parse_windows_video_json(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["memory_total_gib"], 16)
        self.assertEqual(items[0]["memory_source"], "model_profile")
        self.assertEqual(items[0]["support_tier"], "experimental")
        self.assertIn("截断", items[0]["notes"][0])

    def test_windows_vendor_id_recognizes_unlisted_nvidia_and_amd_adapters(self):
        raw = json.dumps(
            [
                {"Name": "Future Compute Adapter", "PNPDeviceID": "PCI\\VEN_10DE&DEV_FF01", "AdapterCompatibility": "Vendor"},
                {"Name": "Professional Graphics Device", "PNPDeviceID": "PCI\\VEN_1002&DEV_FF02", "AdapterCompatibility": "Vendor"},
            ]
        )
        items = parse_windows_video_json(raw)
        self.assertEqual([item["vendor"] for item in items], ["NVIDIA", "AMD"])
        self.assertEqual(items[0]["device_id"], "ff01")
        self.assertEqual(items[1]["vendor_id"], "1002")

    def test_linux_sysfs_discovers_gpu_by_pci_class_and_vendor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nvidia = root / "0000_01_00.0"
            nvidia.mkdir()
            (nvidia / "class").write_text("0x030200\n", encoding="ascii")
            (nvidia / "vendor").write_text("0x10de\n", encoding="ascii")
            (nvidia / "device").write_text("0xffff\n", encoding="ascii")
            items = parse_linux_pci_devices(root)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["vendor"], "NVIDIA")
        self.assertEqual(items[0]["detection_source"], "linux_pci_sysfs")
        self.assertIsNone(items[0]["memory_total_gib"])

    def test_apple_silicon_uses_unified_memory_once(self):
        raw = json.dumps(
            {
                "SPHardwareDataType": [{"chip_type": "Apple M4 Max"}],
                "SPDisplaysDataType": [{"sppci_model": "Apple M4 Max"}],
            }
        )
        chip, items = parse_system_profiler(raw, 64)
        self.assertEqual(chip, "Apple M4 Max")
        self.assertEqual(items[0]["memory_total_gib"], 64)
        self.assertTrue(items[0]["unified_memory"])
        self.assertEqual(items[0]["backend"], "metal")

    def test_macos_amd_uses_system_profiler_vendor_id_and_reported_vram(self):
        raw = json.dumps(
            {
                "SPHardwareDataType": [{"cpu_type": "Intel Core i9"}],
                "SPDisplaysDataType": [
                    {
                        "sppci_model": "Display Controller",
                        "spdisplays_vendor-id": "0x1002",
                        "spdisplays_device-id": "0xabcd",
                        "spdisplays_vram": "16 GB",
                    }
                ],
            }
        )
        _, items = parse_system_profiler(raw, 64)
        self.assertEqual(items[0]["vendor"], "AMD")
        self.assertEqual(items[0]["device_id"], "abcd")
        self.assertEqual(items[0]["memory_total_gib"], 16)


if __name__ == "__main__":
    unittest.main()
