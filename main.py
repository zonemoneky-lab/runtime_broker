from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one runtime agent for each configs/*.json file")
    parser.add_argument("--config-dir", default="configs", help="Directory containing agent JSON config files")
    parser.add_argument("--once", action="store_true", help="Pass --once to each run_agent.py process")
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    if not config_dir.is_absolute():
        config_dir = Path.cwd() / config_dir

    config_files = sorted(path for path in config_dir.glob("*.json") if path.is_file())
    if not config_files:
        print(f"No config files found: {config_dir}\\*.json", file=sys.stderr)
        return 1

    processes: list[tuple[Path, subprocess.Popen[bytes]]] = []
    try:
        for config_file in config_files:
            process = _start_agent(config_file, once=args.once)
            processes.append((config_file, process))
            print(f"Started {config_file} pid={process.pid}")

        return _wait_for_agents(processes)
    except KeyboardInterrupt:
        print("Stopping agents...")
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


def _wait_for_agents(processes: list[tuple[Path, subprocess.Popen[bytes]]]) -> int:
    exit_code = 0
    running = set(range(len(processes)))
    while running:
        for index in list(running):
            config_file, process = processes[index]
            result = process.poll()
            if result is None:
                continue
            running.remove(index)
            print(f"Agent exited {config_file} pid={process.pid} code={result}")
            if result != 0 and exit_code == 0:
                exit_code = result
        if running:
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
