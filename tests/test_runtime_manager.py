from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from agent.config import RuntimeConfig
from agent.runtime_manager import RuntimeManager


class RuntimeManagerTests(unittest.TestCase):
    def test_missing_runtime_dll_is_reported_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            version_dir = Path(temp)
            missing_dll = version_dir / "MissingRuntime.dll"
            manager = RuntimeManager(
                RuntimeConfig(command=["dotnet", str(missing_dll)], working_dir="{version_dir}")
            )

            with self.assertRaisesRegex(RuntimeError, "MissingRuntime.dll"):
                manager.start("abc123", version_dir)

    def test_early_exit_writes_stderr_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            version_dir = root / "version"
            log_dir = root / "logs"
            version_dir.mkdir()
            manager = RuntimeManager(
                RuntimeConfig(
                    command=[
                        sys.executable,
                        "-c",
                        "import sys; sys.stderr.write('runtime boom\\n'); sys.exit(7)",
                    ],
                    working_dir="{version_dir}",
                ),
                log_dir=log_dir,
            )

            with self.assertRaisesRegex(RuntimeError, "Runtime exited with code 7"):
                manager.start("abc123", version_dir)

            stderr_log = log_dir / "runtime-abc123.stderr.log"
            self.assertTrue(stderr_log.exists())
            self.assertIn("runtime boom", stderr_log.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
