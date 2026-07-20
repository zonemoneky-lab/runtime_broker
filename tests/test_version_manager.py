from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from agent.update_manager import compare_versions
from agent.version_manager import VersionManager, sha256_file


class VersionManagerTests(unittest.TestCase):
    def test_install_activate_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_a = root / "a.zip"
            package_b = root / "b.zip"
            _write_zip(package_a, {"repo-a/": b"", "repo-a/app.dll": b"a"})
            _write_zip(package_b, {"repo-b/": b"", "repo-b/app.dll": b"b"})

            manager = VersionManager(root / "state")
            manager.install_zip("v1.0.0", package_a)
            manager.activate("v1.0.0")
            manager.install_zip("v1.0.1", package_b)
            manager.activate("v1.0.1")

            self.assertEqual(manager.current_version(), "v1.0.1")
            self.assertTrue((manager.current_path() / "app.dll").exists())

            rolled_back = manager.rollback()
            self.assertEqual(rolled_back, "v1.0.0")
            self.assertEqual(manager.current_version(), "v1.0.0")

    def test_clear_current_removes_current_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "a.zip"
            _write_zip(package, {"repo-a/app.dll": b"a"})

            manager = VersionManager(root / "state")
            manager.install_zip("v1.0.0", package)
            manager.activate("v1.0.0")
            manager.clear_current()

            self.assertIsNone(manager.current_version())
            self.assertFalse(manager.current_file.exists())

    def test_repairs_existing_single_archive_root_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            nested = state / "versions" / "cc21582c9e8f" / "zonemoneky-lab-file_stub-cc21582c9e8f46eaad63da5c7c8c86b7ea9f814c"
            nested.mkdir(parents=True)
            (nested / "app.dll").write_bytes(b"a")

            manager = VersionManager(state)
            manager.ensure_layout()

            version_dir = state / "versions" / "cc21582c9e8f"
            self.assertTrue((version_dir / "app.dll").exists())
            self.assertFalse(nested.exists())

    def test_sha256_verification_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            file_path = Path(temp) / "payload.bin"
            file_path.write_bytes(b"payload")
            self.assertEqual(sha256_file(file_path), "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5")


class VersionCompareTests(unittest.TestCase):
    def test_semver_like_compare(self) -> None:
        self.assertGreater(compare_versions("v1.2.0", "v1.1.9"), 0)
        self.assertEqual(compare_versions("1.2", "1.2.0"), 0)
        self.assertLess(compare_versions("v1.0.0", "v1.0.1"), 0)
        self.assertGreater(compare_versions("1.0.0", "cc21582c9e8f"), 0)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


if __name__ == "__main__":
    unittest.main()
