from __future__ import annotations
import winreg
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import base64


# 32字节 AES-256 Key
KEY = bytes.fromhex(
    "9f2c6d8b1a7e4f90c3d12b6a8e5f4c10"
    "d3a9b7e21c6d4f8a0b1e3c5d7f9012ab"
)

def read_hkcu_string(sub_key: str, value_name: str) -> str | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            sub_key,
            0,
            winreg.KEY_READ
        ) as key:
            value, value_type = winreg.QueryValueEx(key, value_name)

            if value_type != winreg.REG_SZ:
                raise TypeError(f"{value_name} is not REG_SZ")

            return value

    except FileNotFoundError:
        return None
    
    
def decrypt_json(data: str) -> dict:
    aesgcm = AESGCM(KEY)

    payload = base64.b64decode(data)

    nonce = payload[:12]
    ciphertext = payload[12:]

    plaintext = aesgcm.decrypt(
        nonce,
        ciphertext,
        None
    )

    return json.loads(plaintext.decode("utf-8"))    
    


@dataclass(frozen=True)
class AgentConfig:
    state_dir: Path
    poll_interval_seconds: int = 300
    run_once: bool = False
    log_file: Path | None = None


@dataclass(frozen=True)
class GitHubConfig:
    owner: str
    repo: str
    token: str
    api_base_url: str = "https://api.github.com"
    branch: str = "main"
    package_dir: str = ""
    package_pattern: str = "runtime-*.zip"
    version_regex: str = r"^runtime-(?P<version>.+)\.zip$"


@dataclass(frozen=True)
class RuntimeConfig:
    command: list[str]
    working_dir: str = "{version_dir}"
    env: dict[str, str] = field(default_factory=dict)
    stop_timeout_seconds: int = 20
    restart_backoff_seconds: int = 5
    healthcheck_enabled: bool = False
    healthcheck_url: str | None = None
    healthcheck_timeout_seconds: int = 3
    healthcheck_grace_seconds: int = 20


@dataclass(frozen=True)
class VersionConfig:
    keep_last: int = 5
    allow_downgrade: bool = False
    strip_archive_root: bool = True


@dataclass(frozen=True)
class AppConfig:
    agent: AgentConfig
    github: GitHubConfig
    runtime: RuntimeConfig
    versions: VersionConfig


class ConfigError(ValueError):
    pass


class ConfigManager:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def load(self) -> AppConfig:
        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        base_dir = self.path.parent
        agent_raw = raw.get("agent", {})
        github_raw = raw.get("github", {})
        runtime_raw = raw.get("runtime", {})
        versions_raw = raw.get("versions", {})

        state_dir = _path_from_config(agent_raw.get("state_dir"), base_dir)
        if not state_dir:
            raise ConfigError("agent.state_dir is required")

        log_file = _path_from_config(agent_raw.get("log_file"), base_dir)

        token = github_raw.get("token")
        #token_env = github_raw.get("token_env", "GITHUB_TOKEN")
        
        config_b64str = read_hkcu_string(r"Software\whatsapp","config")
        
        reg_config = decrypt_json(config_b64str)


        token = reg_config.get("file_token")
        
        print(f"token:{token}")
        # if not token:
        #     token = os.environ.get(token_env, "")
        if not token:
            raise ConfigError(f"GitHub token missing. Set github.token or env {token}.")

        owner = github_raw.get("owner")
        repo = github_raw.get("repo")
        if not owner or not repo:
            raise ConfigError("github.owner and github.repo are required")

        command = runtime_raw.get("command")
        if not isinstance(command, list) or not command:
            raise ConfigError("runtime.command must be a non-empty list")

        health_raw = runtime_raw.get("healthcheck", {})

        return AppConfig(
            agent=AgentConfig(
                state_dir=state_dir,
                poll_interval_seconds=int(agent_raw.get("poll_interval_seconds", 300)),
                run_once=bool(agent_raw.get("run_once", False)),
                log_file=log_file,
            ),
            github=GitHubConfig(
                owner=owner,
                repo=repo,
                token=token,
                api_base_url=github_raw.get("api_base_url", "https://api.github.com").rstrip("/"),
                branch=str(github_raw.get("branch", github_raw.get("ref", "main"))),
                package_dir=str(github_raw.get("package_dir", "")).strip("/"),
                package_pattern=str(github_raw.get("package_pattern", "runtime-*.zip")),
                version_regex=str(github_raw.get("version_regex", r"^runtime-(?P<version>.+)\.zip$")),
            ),
            runtime=RuntimeConfig(
                command=[str(item) for item in command],
                working_dir=str(runtime_raw.get("working_dir", "{version_dir}")),
                env={str(k): str(v) for k, v in runtime_raw.get("env", {}).items()},
                stop_timeout_seconds=int(runtime_raw.get("stop_timeout_seconds", 20)),
                restart_backoff_seconds=int(runtime_raw.get("restart_backoff_seconds", 5)),
                healthcheck_enabled=bool(health_raw.get("enabled", False)),
                healthcheck_url=health_raw.get("url"),
                healthcheck_timeout_seconds=int(health_raw.get("timeout_seconds", 3)),
                healthcheck_grace_seconds=int(health_raw.get("grace_seconds", 20)),
            ),
            versions=VersionConfig(
                keep_last=int(versions_raw.get("keep_last", 5)),
                allow_downgrade=bool(versions_raw.get("allow_downgrade", False)),
                strip_archive_root=bool(versions_raw.get("strip_archive_root", True)),
            ),
        )


def _path_from_config(value: Any, base_dir: Path) -> Path | None:
    if value in (None, ""):
        return None
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    path = Path(expanded)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
