from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import BinaryIO
from urllib.request import urlopen

from .config import RuntimeConfig

LOG = logging.getLogger(__name__)


class RuntimeManager:
    def __init__(self, config: RuntimeConfig, log_dir: Path | None = None) -> None:
        self.config = config
        self.log_dir = log_dir
        self.process: subprocess.Popen[bytes] | None = None
        self._stdout_handle: BinaryIO | None = None
        self._stderr_handle: BinaryIO | None = None
        self._stdout_path: Path | None = None
        self._stderr_path: Path | None = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, version: str, version_dir: Path) -> None:
        if self.is_running():
            LOG.info("Runtime already running with pid %s", self.process.pid)
            return

        context = {"version": version, "version_dir": str(version_dir)}
        command = [_format(item, context) for item in self.config.command]
        cwd = Path(_format(self.config.working_dir, context))
        env = os.environ.copy()
        env.update({key: _format(value, context) for key, value in self.config.env.items()})
        _validate_runtime_paths(command, cwd)
        stdout_target, stderr_target = self._open_runtime_logs(version)

        LOG.info("Starting runtime version %s: %s", version, command)
        if self._stdout_path and self._stderr_path:
            LOG.info("Runtime stdout log: %s", self._stdout_path)
            LOG.info("Runtime stderr log: %s", self._stderr_path)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdout=stdout_target,
                stderr=stderr_target,
                creationflags=_windows_creation_flags(),
            )
        except Exception:
            self._close_runtime_logs()
            raise
        if self.config.healthcheck_enabled:
            self.wait_until_healthy()
        else:
            self._raise_if_exited_early()

    def stop(self) -> None:
        if not self.is_running():
            self._close_runtime_logs()
            self.process = None
            return
        assert self.process is not None
        LOG.info("Stopping runtime pid %s", self.process.pid)
        _terminate_process(self.process)
        try:
            self.process.wait(timeout=self.config.stop_timeout_seconds)
        except subprocess.TimeoutExpired:
            LOG.warning("Runtime did not stop gracefully; killing pid %s", self.process.pid)
            self.process.kill()
            self.process.wait(timeout=10)
        finally:
            self.process = None
            self._close_runtime_logs()

    def restart(self, version: str, version_dir: Path) -> None:
        self.stop()
        if self.config.restart_backoff_seconds > 0:
            time.sleep(self.config.restart_backoff_seconds)
        self.start(version, version_dir)

    def ensure_running(self, version: str | None, version_dir: Path | None) -> None:
        if not version or not version_dir:
            LOG.info("No active version; runtime is not started")
            return
        if self.is_running():
            return
        self.start(version, version_dir)

    def wait_until_healthy(self) -> None:
        if not self.config.healthcheck_url:
            raise RuntimeError("runtime.healthcheck.url is required when healthcheck is enabled")
        deadline = time.monotonic() + self.config.healthcheck_grace_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(self._runtime_exit_message())
            try:
                with urlopen(self.config.healthcheck_url, timeout=self.config.healthcheck_timeout_seconds) as response:
                    if 200 <= response.status < 400:
                        return
            except OSError as exc:
                last_error = exc
            time.sleep(1)
        raise RuntimeError(f"Runtime healthcheck failed: {last_error}")

    def _open_runtime_logs(self, version: str) -> tuple[int | BinaryIO, int | BinaryIO]:
        if not self.log_dir:
            return subprocess.DEVNULL, subprocess.DEVNULL
        self.log_dir.mkdir(parents=True, exist_ok=True)
        safe_version = _safe_log_name(version)
        self._stdout_path = self.log_dir / f"runtime-{safe_version}.stdout.log"
        self._stderr_path = self.log_dir / f"runtime-{safe_version}.stderr.log"
        self._stdout_handle = self._stdout_path.open("ab")
        self._stderr_handle = self._stderr_path.open("ab")
        return self._stdout_handle, self._stderr_handle

    def _close_runtime_logs(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle:
                handle.close()
        self._stdout_handle = None
        self._stderr_handle = None

    def _raise_if_exited_early(self) -> None:
        if not self.process:
            return
        deadline = time.monotonic() + 1.5
        while True:
            if self.process.poll() is not None:
                message = self._runtime_exit_message()
                self._close_runtime_logs()
                raise RuntimeError(message)
            if time.monotonic() >= deadline:
                return
            time.sleep(0.1)

    def _runtime_exit_message(self) -> str:
        stderr_hint = f"; stderr={self._stderr_path}" if self._stderr_path else ""
        stdout_hint = f"; stdout={self._stdout_path}" if self._stdout_path else ""
        return f"Runtime exited with code {self.process.returncode if self.process else '<unknown>'}{stderr_hint}{stdout_hint}"


def _format(value: str, context: dict[str, str]) -> str:
    return value.format(**context)


def _validate_runtime_paths(command: list[str], cwd: Path) -> None:
    if not cwd.exists():
        raise RuntimeError(f"Runtime working directory does not exist: {cwd}")
    for index, item in enumerate(command):
        path = Path(item)
        suffix = path.suffix.lower()
        if suffix not in {".dll", ".exe"}:
            continue
        if index == 0 and not path.is_absolute() and not _contains_path_separator(item):
            continue
        candidate = path if path.is_absolute() else cwd / path
        if not candidate.exists():
            raise RuntimeError(
                f"Runtime command references missing file: {candidate}. "
                f"Update runtime.command to a DLL/exe that exists in the package. {_runtime_entry_hint(cwd)}"
            )


def _safe_log_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _contains_path_separator(value: str) -> bool:
    return "/" in value or "\\" in value


def _runtime_entry_hint(cwd: Path) -> str:
    candidates: list[str] = []
    for pattern in ("*.dll", "*.exe"):
        for path in cwd.rglob(pattern):
            if path.is_file():
                candidates.append(str(path.relative_to(cwd)))
                if len(candidates) >= 20:
                    break
        if len(candidates) >= 20:
            break
    if not candidates:
        return f"No .dll/.exe files were found under {cwd}."
    return "Found candidates: " + ", ".join(candidates)


def _windows_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            return
        except Exception:
            pass
    process.terminate()
