from __future__ import annotations

import io
import logging
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from agent.config import GitHubConfig
from agent.github_client import GitHubClient, RequestAborted
from agent.service import AgentService


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class FakeVersions:
    def __init__(self, current: str = "1.0.0") -> None:
        self.current = current
        self.layout_calls = 0
        self.cleanup_calls = 0
        self.events: list[str] = []

    def ensure_layout(self) -> None:
        self.layout_calls += 1

    def current_version(self) -> str | None:
        return self.current

    def current_path(self) -> Path | None:
        return Path("stub") if self.current else None

    def activate(self, version: str) -> None:
        self.current = version
        self.events.append("activate")

    def cleanup_after_activation(self) -> None:
        self.cleanup_calls += 1
        self.events.append("cleanup")


class FakeUpdates:
    def __init__(self, fail_times: int = 0, error: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.checks = 0
        self.error = error or RuntimeError("network unreachable")

    def check(self) -> None:
        self.checks += 1
        if self.checks <= self.fail_times:
            raise self.error
        return None


class FakeRuntime:
    def __init__(self) -> None:
        self.ensure_calls = 0
        self.starts = 0
        self.stops = 0

    def ensure_running(self, version: str | None, path: Path | None) -> None:
        self.ensure_calls += 1

    def start(self, version: str, path: Path) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1


def make_service(
    fail_times: int = 0,
    poll: int = 300,
    error_retry: int = 5,
    error: Exception | None = None,
) -> tuple[AgentService, FakeVersions, FakeUpdates, FakeRuntime]:
    versions = FakeVersions()
    updates = FakeUpdates(fail_times, error)
    runtime = FakeRuntime()
    service = AgentService(
        versions=versions,
        updates=updates,
        runtime=runtime,
        poll_interval_seconds=poll,
        error_retry_seconds=error_retry,
    )
    return service, versions, updates, runtime


class ServiceResilienceTests(unittest.TestCase):
    def test_network_failure_is_retried_and_runtime_stays_alive(self) -> None:
        service, _versions, updates, runtime = make_service(fail_times=1)

        self.assertFalse(service.run_update_cycle_safely())
        self.assertEqual(service.consecutive_failures, 1)
        self.assertEqual(runtime.ensure_calls, 1)

        self.assertTrue(service.run_update_cycle_safely())
        self.assertEqual(service.consecutive_failures, 0)
        self.assertEqual(updates.checks, 2)

    def test_persistent_outage_never_ends_the_loop(self) -> None:
        service, _versions, updates, _runtime = make_service(
            fail_times=999, poll=300, error_retry=1
        )
        sleeps: list[int] = []

        def fake_sleep(seconds: int) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                service.stop()

        service._sleep = fake_sleep
        service.run_forever()

        self.assertEqual(len(sleeps), 4)
        self.assertEqual(updates.checks, 4)
        self.assertEqual(service.consecutive_failures, 4)
        self.assertEqual(sleeps, [1, 2, 4, 8])

    def test_backoff_is_bounded_and_resets_after_recovery(self) -> None:
        service, *_ = make_service(fail_times=3, error_retry=5)
        service.max_error_retry_seconds = 20

        for _ in range(3):
            service.run_update_cycle_safely()
        self.assertEqual(service._next_interval(False), 20)

        service.run_update_cycle_safely()
        self.assertEqual(service._next_interval(True), 300)

    def test_aborted_request_is_not_counted_as_failure(self) -> None:
        service, *_ = make_service(fail_times=1, error=RequestAborted("stopping"))
        self.assertFalse(service.run_update_cycle_safely())
        self.assertEqual(service.consecutive_failures, 0)

    def test_broken_current_version_no_longer_aborts_startup(self) -> None:
        service, versions, updates, runtime = make_service(fail_times=1, error_retry=1)

        def broken_ensure(version: str | None, path: Path | None) -> None:
            runtime.ensure_calls += 1
            if runtime.ensure_calls == 1:
                raise RuntimeError("exe missing")

        runtime.ensure_running = broken_ensure
        sleeps: list[int] = []

        def fake_sleep(seconds: int) -> None:
            sleeps.append(seconds)
            service.stop()

        service._sleep = fake_sleep
        service.run_forever()

        self.assertEqual(len(sleeps), 1)
        self.assertEqual(updates.checks, 1)
        self.assertEqual(versions.current, "1.0.0")
        self.assertEqual(versions.cleanup_calls, 0)

    def test_once_mode_reports_failure_without_raising(self) -> None:
        service, _versions, updates, _runtime = make_service(fail_times=1)
        service.run_forever(run_once=True)
        self.assertFalse(service.last_cycle_succeeded)
        self.assertEqual(updates.checks, 1)

    def test_successful_update_cleans_up_after_new_version_starts(self) -> None:
        service, versions, updates, runtime = make_service()
        package = SimpleNamespace(version="2.0.0")
        prepared = SimpleNamespace(package=package, install_path=Path("new"))
        updates.check = lambda: package
        updates.prepare = lambda _package: prepared

        def record_start(version: str, path: Path) -> None:
            runtime.starts += 1
            versions.events.append("start")

        def record_stop() -> None:
            runtime.stops += 1
            versions.events.append("stop")

        runtime.start = record_start
        runtime.stop = record_stop

        self.assertTrue(service.run_update_cycle_safely())
        self.assertEqual(versions.events, ["stop", "start", "activate", "cleanup"])
        self.assertEqual(versions.current, "2.0.0")
        self.assertEqual(versions.cleanup_calls, 1)

    def test_failed_update_keeps_current_version_without_rollback(self) -> None:
        service, versions, updates, runtime = make_service()
        package = SimpleNamespace(version="2.0.0")
        prepared = SimpleNamespace(package=package, install_path=Path("new"))
        updates.check = lambda: package
        updates.prepare = lambda _package: prepared

        def failing_start(version: str, path: Path) -> None:
            raise RuntimeError("exe missing")

        runtime.start = failing_start

        self.assertFalse(service.run_update_cycle_safely())
        self.assertEqual(versions.current, "1.0.0")
        self.assertEqual(versions.cleanup_calls, 0)
        self.assertEqual(runtime.stops, 2)
        self.assertEqual(runtime.ensure_calls, 1)


class GitHubRetryTests(unittest.TestCase):
    def _client(self, should_abort=None) -> GitHubClient:
        return GitHubClient(
            GitHubConfig(owner="o", repo="r", token="t"),
            max_attempts=3,
            should_abort=should_abort,
        )

    def test_retries_are_bounded_per_cycle(self) -> None:
        client = self._client()
        with patch("agent.github_client.urlopen", side_effect=OSError("network down")) as mocked, patch.object(
            client, "_sleep_or_abort"
        ):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                client._request_bytes("https://example.invalid", "application/vnd.github+json")
        self.assertEqual(mocked.call_count, 3)

    def test_shutdown_aborts_pending_retry(self) -> None:
        stop = [False]
        client = self._client(should_abort=lambda: stop[0])

        def fail_then_stop(*args, **kwargs):
            stop[0] = True
            raise OSError("network down")

        with patch("agent.github_client.urlopen", side_effect=fail_then_stop):
            with self.assertRaises(RequestAborted):
                client._request_bytes("https://example.invalid", "application/vnd.github+json")

    def test_auth_error_fails_fast(self) -> None:
        client = self._client()
        error = HTTPError("https://example.invalid", 401, "Unauthorized", {}, None)
        with patch("agent.github_client.urlopen", side_effect=error) as mocked:
            with self.assertRaises(HTTPError):
                client._request_bytes("https://example.invalid", "application/vnd.github+json")
        self.assertEqual(mocked.call_count, 1)


class FakeProcess:
    def __init__(self, results: list[int]) -> None:
        self.results = list(results)
        self.pid = 1234

    def poll(self) -> int:
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]


class LauncherRestartTests(unittest.TestCase):
    def _policy(self, **kwargs):
        import main as launcher

        defaults = {
            "max_restarts": 3,
            "backoff_seconds": 0.0,
            "max_backoff_seconds": 0.0,
            "reset_after_seconds": 300.0,
        }
        defaults.update(kwargs)
        return launcher.RestartPolicy(**defaults)

    def test_crashed_child_is_restarted_with_backoff(self) -> None:
        import main as launcher

        first = FakeProcess([1])
        second = FakeProcess([0])
        with patch.object(launcher, "_start_agent", return_value=second) as start, patch.object(
            launcher.time, "sleep"
        ), patch.object(launcher.time, "monotonic", return_value=0.0):
            code = launcher._wait_for_agents(
                [(Path("cfg.json"), first)], policy=self._policy()
            )
        self.assertEqual(code, 0)
        start.assert_called_once_with(Path("cfg.json"), once=False)

    def test_gives_up_after_max_restarts(self) -> None:
        import main as launcher

        errors = io.StringIO()
        with patch.object(launcher, "_start_agent", return_value=FakeProcess([1])) as start, patch.object(
            launcher.time, "sleep"
        ), patch.object(launcher.time, "monotonic", return_value=0.0), redirect_stderr(errors):
            code = launcher._wait_for_agents(
                [(Path("cfg.json"), FakeProcess([1]))],
                policy=self._policy(max_restarts=1),
            )
        self.assertEqual(code, 1)
        self.assertEqual(start.call_count, 1)
        self.assertIn("Giving up", errors.getvalue())

    def test_zero_max_restarts_disables_restarting(self) -> None:
        import main as launcher

        with patch.object(launcher, "_start_agent") as start, patch.object(
            launcher.time, "sleep"
        ), patch.object(launcher.time, "monotonic", return_value=0.0):
            code = launcher._wait_for_agents(
                [(Path("cfg.json"), FakeProcess([1]))],
                policy=self._policy(max_restarts=0),
            )
        self.assertEqual(code, 1)
        start.assert_not_called()

    def test_once_mode_never_restarts(self) -> None:
        import main as launcher

        with patch.object(launcher, "_start_agent") as start, patch.object(
            launcher.time, "sleep"
        ), patch.object(launcher.time, "monotonic", return_value=0.0):
            code = launcher._wait_for_agents(
                [(Path("cfg.json"), FakeProcess([3]))],
                once=True,
                policy=self._policy(),
            )
        self.assertEqual(code, 3)
        start.assert_not_called()

    def test_backoff_is_capped(self) -> None:
        import main as launcher

        policy = launcher.RestartPolicy(backoff_seconds=5.0, max_backoff_seconds=60.0)
        self.assertEqual(
            [policy.delay_for(i) for i in range(1, 8)],
            [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0],
        )

    def test_stable_run_resets_failure_counter(self) -> None:
        import main as launcher

        clock = [0.0]
        third = FakeProcess([0])

        class StableThenCrash(FakeProcess):
            def __init__(self) -> None:
                super().__init__([1])
                self.calls = 0

            def poll(self):
                self.calls += 1
                if self.calls == 1:
                    clock[0] = 701.0
                    return None
                return 1

        sequence = iter([StableThenCrash(), third])

        def restart(*args, **kwargs):
            clock[0] = 400.0
            return next(sequence)

        with patch.object(launcher, "_start_agent", side_effect=restart) as start, patch.object(
            launcher.time, "sleep"
        ), patch.object(launcher.time, "monotonic", side_effect=lambda: clock[0]):
            code = launcher._wait_for_agents(
                [(Path("cfg.json"), FakeProcess([1]))],
                policy=self._policy(max_restarts=1),
            )
        self.assertEqual(code, 0)
        self.assertEqual(start.call_count, 2)


if __name__ == "__main__":
    unittest.main()
