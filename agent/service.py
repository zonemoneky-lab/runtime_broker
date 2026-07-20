from __future__ import annotations

import logging
import time

from .runtime_manager import RuntimeManager
from .update_manager import UpdateManager
from .version_manager import VersionManager

LOG = logging.getLogger(__name__)


class AgentService:
    def __init__(
        self,
        versions: VersionManager,
        updates: UpdateManager,
        runtime: RuntimeManager,
        poll_interval_seconds: int,
    ) -> None:
        self.versions = versions
        self.updates = updates
        self.runtime = runtime
        self.poll_interval_seconds = poll_interval_seconds
        self._stopping = False

    def run_forever(self, run_once: bool = False) -> None:
        self.versions.ensure_layout()
        self._ensure_current_runtime()
        while not self._stopping:
            self.run_update_cycle()
            if run_once:
                return
            self._sleep()

    def stop(self) -> None:
        self._stopping = True
        self.runtime.stop()

    def run_update_cycle(self) -> None:
        package = self.updates.check()
        if package is None:
            self.runtime.ensure_running(self.versions.current_version(), self.versions.current_path())
            return

        prepared = self.updates.prepare(package)
        old_version = self.versions.current_version()
        old_path = self.versions.current_path()
        try:
            self.runtime.stop()
            self.runtime.start(prepared.package.version, prepared.install_path)
            self.versions.activate(prepared.package.version)
            self.versions.cleanup_old_versions()
        except Exception as exc:
            LOG.error("Failed to activate version %s: %s", prepared.package.version, exc)
            self.runtime.stop()
            if old_version and old_path:
                LOG.warning("Keeping previous version %s active after failed update", old_version)
                self.versions.activate(old_version)
                self.runtime.start(old_version, old_path)
            else:
                self.versions.clear_current()
            raise

    def _sleep(self) -> None:
        deadline = time.monotonic() + self.poll_interval_seconds
        while not self._stopping and time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))

    def _ensure_current_runtime(self) -> None:
        try:
            self.runtime.ensure_running(self.versions.current_version(), self.versions.current_path())
        except Exception as exc:
            LOG.error("Failed to start current version %s: %s", self.versions.current_version(), exc)
            self.runtime.stop()
            rollback_version = self.versions.rollback()
            rollback_path = self.versions.current_path()
            if rollback_version and rollback_path:
                LOG.warning("Rolled back current version to %s", rollback_version)
                self.runtime.start(rollback_version, rollback_path)
            else:
                self.versions.clear_current()
                raise
