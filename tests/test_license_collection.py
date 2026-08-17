import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "Collect-ThirdPartyLicenses.py"
SPEC = importlib.util.spec_from_file_location("vsg_license_collection", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_cpython_license() -> bytes:
    return (
        b"PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\n"
        b"This LICENSE AGREEMENT covers Python.\n"
        + (b"license text\n" * 100)
    )


class CPythonLicenseDiscoveryTests(unittest.TestCase):
    def test_python_org_macos_application_license_is_a_bounded_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = MODULE._cpython_license_candidates(
                override="",
                base_prefix=root / "Library" / "Frameworks" / "Python.framework" / "Versions" / "3.12",
                prefix=root / "venv",
                executable=root / "venv" / "bin" / "python3",
                platform_name="darwin",
                major_minor="3.12",
                applications_root=root / "Applications",
            )
            self.assertIn(root / "Applications" / "Python 3.12" / "License.rtf", candidates)
            self.assertEqual(len(candidates), len(set(candidates)))

    def test_python_org_macos_rtf_license_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            license_path = Path(directory) / "Applications" / "Python 3.12" / "License.rtf"
            license_path.parent.mkdir(parents=True)
            content = valid_cpython_license()
            license_path.write_bytes(content)

            selected = MODULE._select_cpython_license([license_path], "3.12.10")

            self.assertEqual(selected, ("CPython", "3.12.10", content, "License.rtf"))

    def test_unrelated_file_is_never_accepted_as_cpython_license(self):
        with tempfile.TemporaryDirectory() as directory:
            unrelated = Path(directory) / "LICENSE.txt"
            unrelated.write_bytes(b"not the CPython license" * 100)

            with self.assertRaisesRegex(RuntimeError, "official macOS"):
                MODULE._select_cpython_license([unrelated], "3.12.10")


if __name__ == "__main__":
    unittest.main()
