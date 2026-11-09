from __future__ import annotations

import logging
import time

from .github_client import RequestAborted
from .runtime_manager import RuntimeManager
from .update_manager import UpdateManager
from .version_manager import VersionManager

LOG = logging.getLogger(__name__)


class AgentService:
    """Poll for runtime updates and keep the managed process alive.

    A failed poll is never fatal: network hiccups, bad packages, disk errors and
    startup failures are logged and retried on the next cycle. There is no
    version rollback - the previously activated version simply stays current
    until a new one starts successfully. Only an explicit stop() (or run_once)
    ends the loop.
    """

    def __init__(
        self,
        versions: VersionManager,
        updates: UpdateManager,
        runtime: RuntimeManager,
        poll_interval_seconds: int,
        error_retry_seconds: int = 5,
        max_error_retry_seconds: int = 300,
    ) -> None:
        self.versions = versions
        self.updates = updates
        self.runtime = runtime
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.error_retry_seconds = max(1, int(error_retry_seconds))
        self.max_error_retry_seconds = max(self.error_retry_seconds, int(max_error_retry_seconds))
        self._stopping = False
        self.consecutive_failures = 0
        self.last_cycle_succeeded = True

    def run_forever(self, run_once: bool = False) -> None:
        self._safe_ensure_layout()
        self._ensure_current_runtime()
        while not self._stopping:
            succeeded = self.run_update_cycle_safely()
            if run_once:
                return
            self._sleep(self._next_interval(succeeded))

    def stop(self) -> None:
        self._stopping = True
        try:
            self.runtime.stop()
        except Exception:
            LOG.exception("Failed to stop runtime cleanly during shutdown")

    def run_update_cycle_safely(self) -> bool:
        """Run one cycle, converting any recoverable failure into a retry."""
        try:
            self.run_update_cycle()
        except RequestAborted:
            LOG.info("Update cycle interrupted by shutdown request")
            succeeded = False
        except Exception:
            self.consecutive_failures += 1
            LOG.exception(
                "Update cycle failed (%d consecutive); agent stays alive, retrying in %ss",
                self.consecutive_failures,
                self._next_interval(False),
            )
            self._recover_runtime()
            succeeded = False
        else:
            if self.consecutive_failures:
                LOG.info(
                    "Update cycle recovered after %d consecutive failure(s)",
                    self.consecutive_failures,
                )
            self.consecutive_failures = 0
            succeeded = True
        self.last_cycle_succeeded = succeeded
        return succeeded

    def run_update_cycle(self) -> None:
        package = self.updates.check()
        if package is None:
            self.runtime.ensure_running(self.versions.current_version(), self.versions.current_path())
            return

        prepared = self.updates.prepare(package)
        try:
            self.runtime.stop()
            self.runtime.start(prepared.package.version, prepared.install_path)
            self.versions.activate(prepared.package.version)
            self.versions.cleanup_after_activation()
        except Exception as exc:
            LOG.error("Failed to activate version %s: %s", prepared.package.version, exc)
            self.runtime.stop()
            raise

    def _next_interval(self, succeeded: bool) -> int:
        if succeeded:
            return self.poll_interval_seconds
        backoff = self.error_retry_seconds * (2 ** min(max(self.consecutive_failures - 1, 0), 10))
        return min(backoff, self.max_error_retry_seconds)

    def _sleep(self, seconds: int) -> None:
        deadline = time.monotonic() + max(0, seconds)
        while not self._stopping and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    def _safe_ensure_layout(self) -> None:
        try:
            self.versions.ensure_layout()
        except Exception:
            LOG.exception("Failed to prepare state layout; retrying during the next cycle")

    def _recover_runtime(self) -> None:
        """Keep the active version running after a failed cycle; never roll back."""
        try:
            self.runtime.ensure_running(self.versions.current_version(), self.versions.current_path())
        except Exception:
            LOG.exception("Active version could not be started; will retry on the next cycle")

    def _ensure_current_runtime(self) -> None:
        try:
            self.runtime.ensure_running(self.versions.current_version(), self.versions.current_path())
        except Exception:
            LOG.exception(
                "Failed to start current version %s; will retry on the next cycle",
                self.versions.current_version(),
            )
            try:
                self.runtime.stop()
            except Exception:
                LOG.exception("Failed to stop runtime after a startup failure")
