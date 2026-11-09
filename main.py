from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RestartPolicy:
    """Bounded and visible restart policy for crashed agent processes.

    This launcher installs no service, writes no startup entry and hides no
    window. Restarts are capped, printed with their reason and exit code,
    skipped in --once mode, and abandoned after repeated immediate crashes so
    a broken configuration cannot loop forever in the background.
    """

    max_restarts: int = 3
    backoff_seconds: float = 5.0
    max_backoff_seconds: float = 60.0
    reset_after_seconds: float = 300.0

    def delay_for(self, consecutive_failures: int) -> float:
        exponent = min(max(consecutive_failures - 1, 0), 6)
        return min(self.backoff_seconds * (2**exponent), self.max_backoff_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one runtime agent for each configs/*.json file")
    parser.add_argument("--config-dir", default="configs", help="Directory containing agent JSON config files")
    parser.add_argument("--once", action="store_true", help="Pass --once to each run_agent.py process")
    parser.add_argument(
        "--max-restarts", type=int, default=3,
        help="Consecutive restarts allowed per agent before giving up (0 disables restarts)",
    )
    parser.add_argument("--restart-backoff", type=float, default=5.0, help="Initial restart delay in seconds")
    parser.add_argument("--max-restart-backoff", type=float, default=60.0, help="Restart delay cap in seconds")
    parser.add_argument(
        "--restart-reset-after", type=float, default=300.0,
        help="Forget consecutive failures after this many seconds of stable running",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    if not config_dir.is_absolute():
        config_dir = Path.cwd() / config_dir

    config_files = sorted(path for path in config_dir.glob("*.json") if path.is_file())
    if not config_files:
        print(f"No config files found: {config_dir}\\*.json", file=sys.stderr)
        return 1

    policy = RestartPolicy(
        max_restarts=max(0, args.max_restarts),
        backoff_seconds=max(0.0, args.restart_backoff),
        max_backoff_seconds=max(0.0, args.max_restart_backoff),
        reset_after_seconds=max(1.0, args.restart_reset_after),
    )
    processes: list[tuple[Path, subprocess.Popen[bytes]]] = []
    try:
        for config_file in config_files:
            process = _start_agent(config_file, once=args.once)
            processes.append((config_file, process))
            print(f"Started {config_file} pid={process.pid}", flush=True)

        return _wait_for_agents(processes, once=args.once, policy=policy)
    except KeyboardInterrupt:
        print("Stopping agents...", flush=True)
        _stop_agents(processes)
        return 130
    except Exception:
        _stop_agents(processes)
        raise


def _start_agent(config_file: Path, once: bool) -> subprocess.Popen[bytes]:
    run_agent_path = Path(__file__).resolve().with_name("run_agent.py")
    command = [sys.executable, "-u", str(run_agent_path), "--config", str(config_file)]
    if once:
        command.append("--once")

    kwargs: dict[str, object] = {"cwd": str(Path.cwd())}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(command, **kwargs)


def _wait_for_agents(
    processes: list[tuple[Path, subprocess.Popen[bytes]]],
    *,
    once: bool = False,
    policy: RestartPolicy | None = None,
) -> int:
    """Wait for all agents, restarting crashed ones within a bounded policy.

    Every restart is printed with its exit code and attempt number. Repeated
    immediate crashes end with an explicit 'giving up' message and a non-zero
    exit code rather than an endless self-healing loop. Other healthy agents
    are left running when one of them is given up on.
    """
    policy = policy or RestartPolicy()
    started = time.monotonic()
    state = ["running"] * len(processes)  # running | waiting | finished | given_up
    failures = [0] * len(processes)
    retry_at = [0.0] * len(processes)
    started_at = [started] * len(processes)
    exit_code = 0

    def active() -> bool:
        return any(item in {"running", "waiting"} for item in state)

    while active():
        now = time.monotonic()
        for index, (config_file, process) in enumerate(processes):
            if state[index] != "running":
                continue
            if failures[index] and now - started_at[index] >= policy.reset_after_seconds:
                print(f"Agent {config_file} stayed up; resetting its restart counter", flush=True)
                failures[index] = 0
            result = process.poll()
            if result is None:
                continue
            print(f"Agent exited {config_file} pid={process.pid} code={result}", flush=True)
            if once or result == 0:
                state[index] = "finished"
                if result != 0 and exit_code == 0:
                    exit_code = result
                continue
            if policy.max_restarts <= 0 or failures[index] >= policy.max_restarts:
                state[index] = "given_up"
                if exit_code == 0:
                    exit_code = result or 1
                if policy.max_restarts <= 0:
                    reason = "restarts are disabled (--max-restarts 0)"
                else:
                    reason = (
                        f"{failures[index]} consecutive crashes reached the limit "
                        f"of {policy.max_restarts}"
                    )
                print(
                    f"Giving up on {config_file}: {reason}. Not restarting; "
                    "check the agent log for the cause.",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            failures[index] += 1
            delay = policy.delay_for(failures[index])
            retry_at[index] = now + delay
            state[index] = "waiting"
            print(
                f"Restarting {config_file} in {delay:g}s "
                f"(attempt {failures[index]}/{policy.max_restarts}, exit code {result})",
                flush=True,
            )

        for index, (config_file, _process) in enumerate(processes):
            if state[index] != "waiting" or time.monotonic() < retry_at[index]:
                continue
            try:
                new_process = _start_agent(config_file, once=False)
            except Exception as exc:
                print(f"Could not restart {config_file}: {exc}", file=sys.stderr, flush=True)
                if policy.max_restarts <= 0 or failures[index] >= policy.max_restarts:
                    state[index] = "given_up"
                    if exit_code == 0:
                        exit_code = 1
                    print(f"Giving up on {config_file}: restart failed", file=sys.stderr, flush=True)
                    continue
                failures[index] += 1
                retry_at[index] = time.monotonic() + policy.delay_for(failures[index])
                continue
            processes[index] = (config_file, new_process)
            started_at[index] = time.monotonic()
            state[index] = "running"
            print(f"Restarted {config_file} pid={new_process.pid}", flush=True)

        if active():
            time.sleep(1)
    return exit_code


def _stop_agents(processes: list[tuple[Path, subprocess.Popen[bytes]]]) -> None:
    for _config_file, process in processes:
        if process.poll() is None:
            _terminate_process(process)

    deadline = time.monotonic() + 10
    for _config_file, process in processes:
        remaining = max(deadline - time.monotonic(), 0)
        if process.poll() is None:
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            return
        except OSError:
            pass
    process.terminate()


if __name__ == "__main__":
    sys.exit(main())
