import json
import unittest

from vsg.posture import parse_linux_firewall, parse_windows_firewall


class PostureTests(unittest.TestCase):
    def test_windows_firewall_maps_exact_and_broad_allow_rules(self):
        profiles = json.dumps({"Name": "Public", "Enabled": True, "DefaultInboundAction": "Block", "DefaultOutboundAction": "Allow"})
        rules = json.dumps(
            [
                {"DisplayName": "Model API", "Profile": "Private", "Protocol": "TCP", "LocalPort": "8000"},
                {"DisplayName": "Broad", "Profile": "Any", "Protocol": "TCP", "LocalPort": "Any"},
            ]
        )
        value = parse_windows_firewall(profiles, rules, {8000, 9000})
        self.assertEqual(len(value["inbound_allow_matches"]["8000"]), 2)
        self.assertEqual(len(value["inbound_allow_matches"]["9000"]), 1)
        self.assertEqual(value["broad_allow_rule_count"], 1)

    def test_linux_ufw_maps_explicit_tcp_allow_rule(self):
        value = parse_linux_firewall(
            "Status: active\n8000/tcp ALLOW Anywhere\n22/tcp ALLOW 192.168.1.0/24\n",
            {8000, 9000},
            "ufw",
            True,
        )
        self.assertEqual(value["platform"], "linux")
        self.assertTrue(value["all_profiles_enabled"])
        self.assertEqual(len(value["inbound_allow_matches"]["8000"]), 1)
        self.assertEqual(value["inbound_allow_matches"]["9000"], [])


if __name__ == "__main__":
    unittest.main()
