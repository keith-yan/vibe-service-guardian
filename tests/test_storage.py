import tempfile
import unittest
from pathlib import Path

from vsg.models import ProcessSnapshot, ServiceRecord
from vsg.storage import Storage


def service(pid: int, created: float, fingerprint: str = "same") -> ServiceRecord:
    return ServiceRecord(
        id=f"host:{pid}",
        fingerprint=fingerprint,
        source="host",
        display_name="demo",
        runtime="Python",
        process=ProcessSnapshot(pid=pid, create_time=created),
    )


class StorageTests(unittest.TestCase):
    def test_single_process_restart_increments_once(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                storage.observe([service(1, 100)], now=200)
                storage.observe([service(2, 300)], now=400)
                self.assertEqual(storage.histories(["same"])["same"]["restart_count"], 1)
            finally:
                storage.close()

    def test_concurrent_identical_processes_do_not_look_like_restart_storm(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                values = [service(1, 100), service(2, 150)]
                storage.observe(values, now=200)
                storage.observe(values, now=205)
                self.assertEqual(storage.histories(["same"])["same"]["restart_count"], 0)
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
