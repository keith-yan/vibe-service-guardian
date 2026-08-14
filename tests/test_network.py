import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from vsg.network import collect_connections, parse_lsof_fields


LSOF_SAMPLE = """p101
cpython3
f9
PTCP
n127.0.0.1:8000
TST=LISTEN
f10
PTCP
n192.168.1.10:54000->1.1.1.1:443
TST=ESTABLISHED
p202
cnode
f18
PTCP
n[::]:3000
TST=LISTEN
f19
PUDP
n*:5353
"""


class NetworkTests(unittest.TestCase):
    def test_lsof_field_parser_maps_tcp_udp_and_established(self):
        endpoints, established = parse_lsof_fields(LSOF_SAMPLE, include_udp=True)
        self.assertEqual(established[101], 1)
        first = {(item.protocol, item.address, item.port, item.state) for item in endpoints[101]}
        second = {(item.protocol, item.address, item.port, item.state) for item in endpoints[202]}
        self.assertIn(("TCP", "127.0.0.1", 8000, "LISTEN"), first)
        self.assertIn(("TCP", "::", 3000, "LISTEN"), second)
        self.assertIn(("UDP", "*", 5353, "BOUND"), second)

    def test_lsof_parser_can_exclude_udp(self):
        endpoints, _ = parse_lsof_fields(LSOF_SAMPLE, include_udp=False)
        self.assertFalse(any(item.protocol == "UDP" for items in endpoints.values() for item in items))

    def test_macos_collector_reports_non_privileged_visibility(self):
        with (
            patch("vsg.network.shutil.which", return_value="/usr/sbin/lsof"),
            patch(
                "vsg.network.subprocess.run",
                return_value=CompletedProcess(["lsof"], 0, stdout=LSOF_SAMPLE, stderr=""),
            ),
        ):
            endpoints, _, errors, status = collect_connections(False, system_name="Darwin")
        self.assertFalse(errors)
        self.assertIn(101, endpoints)
        self.assertEqual(status["method"], "lsof")
        self.assertEqual(status["visibility"], "current_user")
        self.assertEqual(status["status"], "partial")


if __name__ == "__main__":
    unittest.main()
