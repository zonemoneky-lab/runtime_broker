from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .github_client import GitHubClient, RemotePackage
from .version_compare import compare_versions
from .version_manager import VersionManager

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedUpdate:
    package: RemotePackage
    package_path: Path
    install_path: Path


class UpdateManager:
    def __init__(self, github: GitHubClient, versions: VersionManager, allow_downgrade: bool = False) -> None:
        self.github = github
        self.versions = versions
        self.allow_downgrade = allow_downgrade

    def check(self) -> RemotePackage | None:
        remote = self.github.get_remote_package()
        current = self.versions.current_version()
        if not current:
            LOG.info("No current version; remote %s will be installed", remote.version)
            return remote
        cmp_result = compare_versions(remote.version, current)
        if remote.version == current:
            LOG.info("Current version %s is already up to date", current)
            return None
        if cmp_result < 0 and not self.allow_downgrade:
            LOG.warning("Remote version %s is older than current %s; skipping", remote.version, current)
            return None
        if cmp_result == 0 and remote.version != current and not self.allow_downgrade:
            LOG.info("Remote package %s differs from current %s; treating it as update", remote.version, current)
        return remote

    def prepare(self, package: RemotePackage) -> PreparedUpdate:
        self.versions.ensure_layout()
        archive_path = self.versions.downloads_dir / f"{_safe_download_name(package.version)}.zip"
        self.github.download_package(package, archive_path)
        install_path = self.versions.install_zip(package.version, archive_path)
        return PreparedUpdate(package=package, package_path=archive_path, install_path=install_path)

def _safe_download_name(version: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in version)
