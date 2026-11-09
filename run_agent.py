from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from agent.config import ConfigManager
from agent.github_client import GitHubClient
from agent.logging_setup import configure_logging
from agent.runtime_manager import RuntimeManager
from agent.service import AgentService
from agent.single_instance import SingleInstanceLock
from agent.update_manager import UpdateManager
from agent.version_manager import VersionManager


def build_service(
    config_path: str, stop_event: threading.Event | None = None
) -> tuple[AgentService, bool, SingleInstanceLock]:
    config = ConfigManager(config_path).load()
    configure_logging(config.agent.log_file)
    resolved_config_path = Path(config_path).resolve()
    modified = resolved_config_path.stat().st_mtime if resolved_config_path.exists() else None
    logging.getLogger("agent").info("Loaded config: %s", resolved_config_path)
    logging.getLogger("agent").info("Config modified timestamp: %s", modified)
    logging.getLogger("agent").info("Runtime command template: %s", config.runtime.command)

    versions = VersionManager(
        state_dir=config.agent.state_dir,
        strip_archive_root=config.versions.strip_archive_root,
        platform=config.versions.platform,
    )
    logging.getLogger("agent").info("Runtime platform: %s", config.versions.platform or "<default>")
    github = GitHubClient(
        config.github,
        should_abort=stop_event.is_set if stop_event else None,
    )
    updates = UpdateManager(github, versions, allow_downgrade=config.versions.allow_downgrade)
    runtime = RuntimeManager(config.runtime, log_dir=versions.platform_dir / "runtime-logs")
    service = AgentService(
        versions=versions,
        updates=updates,
        runtime=runtime,
        poll_interval_seconds=config.agent.poll_interval_seconds,
    )
    return service, config.agent.run_once, SingleInstanceLock(versions.platform_dir / "agent.lock")


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent runtime update agent")
    parser.add_argument("--config", help="Path to agent JSON config", default=r".\config.example.json")
    parser.add_argument("--once", action="store_true", help="Run one update cycle and exit")
    args = parser.parse_args()

    stop_event = threading.Event()
    try:
        service, config_run_once, lock = build_service(args.config, stop_event)

        def handle_stop(_signum: int, _frame: object) -> None:
            stop_event.set()
            service.stop()

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

        run_once = args.once or config_run_once
        with lock:
            service.run_forever(run_once=run_once)
        if run_once and not service.last_cycle_succeeded:
            logging.getLogger("agent").error("One-shot update cycle failed")
            return 1
        return 0
    except Exception:
        logging.getLogger("agent").exception("Agent stopped unexpectedly")
        return 1


if __name__ == "__main__":
    sys.exit(main())
