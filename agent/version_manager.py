from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstalledVersion:
    version: str
    path: Path


class VersionManager:
    def __init__(
        self,
        state_dir: Path,
        strip_archive_root: bool = True,
        platform: str | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.platform = platform.strip() if platform else None
        self.strip_archive_root = strip_archive_root
        self.platform_dir = state_dir / _safe_platform_dir_name(self.platform) if self.platform else state_dir
        self.versions_dir = self.platform_dir / "versions"
        self.downloads_dir = self.platform_dir / "downloads"
        self.current_file = self.platform_dir / "current.txt"
        self.history_file = self.platform_dir / "history.json"

    def ensure_layout(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.platform_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            self._write_json(self.history_file, [])
        if self.strip_archive_root:
            self.repair_installed_layouts()

    def current_version(self) -> str | None:
        if not self.current_file.exists():
            return None
        value = self.current_file.read_text(encoding="utf-8").strip()
        return value or None

    def current_path(self) -> Path | None:
        version = self.current_version()
        if not version:
            return None
        path = self.path_for(version)
        return path if path.exists() else None

    def path_for(self, version: str) -> Path:
        return self.versions_dir / _safe_version_dir_name(version)

    def installed_versions(self) -> list[InstalledVersion]:
        if not self.versions_dir.exists():
            return []
        versions: list[InstalledVersion] = []
        for path in self.versions_dir.iterdir():
            if path.is_dir() and not path.name.startswith(".staging-"):
                versions.append(InstalledVersion(version=path.name, path=path))
        return sorted(versions, key=lambda item: item.path.stat().st_mtime, reverse=True)

    def repair_installed_layouts(self) -> None:
        for installed in self.installed_versions():
            _flatten_single_root_dir(installed.path)

    def verify_sha256(self, file_path: Path, expected_sha256: str) -> None:
        actual = sha256_file(file_path)
        if actual.lower() != expected_sha256.lower():
            raise RuntimeError(f"SHA256 mismatch for {file_path.name}: expected {expected_sha256}, got {actual}")

    def install_zip(self, version: str, zip_path: Path) -> Path:
        self.ensure_layout()
        final_dir = self.path_for(version)
        if final_dir.exists():
            LOG.info("Version %s already installed at %s", version, final_dir)
            return final_dir

        staging_dir = self.versions_dir / f".staging-{_safe_version_dir_name(version)}-{uuid.uuid4().hex}"
        staging_dir.mkdir(parents=True, exist_ok=False)
        try:
            self._extract_zip(zip_path, staging_dir)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            staging_dir.replace(final_dir)
            LOG.info("Installed version %s at %s", version, final_dir)
            return final_dir
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    def activate(self, version: str) -> None:
        version_dir = self.path_for(version)
        if not version_dir.exists():
            raise RuntimeError(f"Cannot activate missing version {version}")

        previous = self.current_version()
        tmp = self.current_file.with_suffix(".tmp")
        tmp.write_text(version + os.linesep, encoding="utf-8")
        tmp.replace(self.current_file)
        self._append_history("activate", version, previous)
        LOG.info("Activated version %s (previous=%s)", version, previous)

    def clear_current(self) -> None:
        previous = self.current_version()
        if self.current_file.exists():
            self.current_file.unlink()
        self._append_history("clear_current", None, previous)
        LOG.warning("Cleared current version (previous=%s)", previous)

    def cleanup_after_activation(self) -> None:
        """Remove every non-active version, staging leftover and archive.

        Called only after the new version has been started and activated.
        Cleanup is best-effort: a stale file must never abort a good update.
        """
        try:
            self._prune_after_activation()
        except Exception:
            LOG.exception("Cleanup after activation failed; the active version is unaffected")

    def _prune_after_activation(self) -> None:
        current_path = self.current_path()
        if current_path is None:
            LOG.warning("No active version; skipping cleanup")
            return
        for directory, label, keep in (
            (self.versions_dir, "non-active version", current_path),
            (self.downloads_dir, "downloaded archive", None),
        ):
            try:
                if not directory.exists():
                    continue
                for path in directory.iterdir():
                    if keep is not None and path == keep:
                        continue
                    self._remove_entry(path, label)
            except OSError as exc:
                LOG.warning("Could not clean %s: %s", directory, exc)

    @staticmethod
    def _remove_entry(path: Path, label: str) -> None:
        LOG.info("Removing %s %s", label, path.name)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return
        try:
            path.unlink()
        except OSError as exc:
            LOG.warning("Could not remove %s: %s", path, exc)

    def _extract_zip(self, zip_path: Path, destination: Path) -> None:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            prefix = _common_top_level_prefix([member.filename for member in members]) if self.strip_archive_root else ""
            for member in members:
                if member.is_dir():
                    continue
                relative = member.filename
                if prefix and relative.startswith(prefix):
                    relative = relative[len(prefix) :]
                if not relative:
                    continue
                target = destination / relative
                resolved = target.resolve()
                if not _is_relative_to(resolved, destination.resolve()):
                    raise RuntimeError(f"Unsafe zip entry blocked: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _append_history(self, action: str, version: str | None, previous: str | None) -> None:
        history = self._read_history()
        history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "version": version,
                "previous": previous,
            }
        )
        self._write_json(self.history_file, history[-200:])

    def _read_history(self) -> list[dict[str, str | None]]:
        if not self.history_file.exists():
            return []
        with self.history_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json(self, path: Path, value: object) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
        tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_version_dir_name(version: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in version)
    if not safe:
        raise ValueError("Version cannot be empty")
    return safe


def _safe_platform_dir_name(platform: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in platform)
    if not safe or safe in {".", ".."}:
        raise ValueError("Platform cannot be empty")
    return safe


def _common_top_level_prefix(names: list[str]) -> str:
    normalized = [name.replace("\\", "/").strip("/") for name in names if name.strip("/")]
    if not normalized:
        return ""
    top_levels = {name.split("/", 1)[0] for name in normalized}
    if len(top_levels) != 1:
        return ""
    top_level = next(iter(top_levels))
    has_nested_content = any(name.startswith(top_level + "/") for name in normalized)
    if has_nested_content and all(name == top_level or name.startswith(top_level + "/") for name in normalized):
        return top_level + "/"
    return ""


def _flatten_single_root_dir(version_dir: Path) -> None:
    children = list(version_dir.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return

    root_dir = children[0]
    root_children = list(root_dir.iterdir())
    for child in root_children:
        target = version_dir / child.name
        if target.exists():
            raise RuntimeError(f"Cannot flatten {version_dir}; target already exists: {target}")
        shutil.move(str(child), str(target))
    root_dir.rmdir()
    LOG.info("Flattened archive root %s into %s", root_dir.name, version_dir)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
