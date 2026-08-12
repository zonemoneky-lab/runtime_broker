from __future__ import annotations

import fnmatch
import json
import logging
import re
import time
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import GitHubConfig
from .version_compare import compare_versions

LOG = logging.getLogger(__name__)

from pathlib import Path
import ssl

base_path = Path(__file__).resolve().parent.parent
cafile = base_path / "certs" / "cacert.pem"

ssl_context = ssl.create_default_context(
    cafile=str(cafile)
)


@dataclass(frozen=True)
class RemotePackage:
    version: str
    download_url: str
    branch: str
    path: str
    sha: str
    download_accept: str = "application/vnd.github.raw"


class GitHubClient:
    def __init__(self, config: GitHubConfig) -> None:
        self.config = config

    def get_remote_package(self) -> RemotePackage:
        files = self._list_package_files()
        candidates = [self._package_from_content_item(item) for item in files if self._is_package_candidate(item)]
        if not candidates:
            location = self.config.package_dir or "."
            raise RuntimeError(
                f"No runtime package matched {self.config.package_pattern!r} in "
                f"{self.config.owner}/{self.config.repo}@{self.config.branch}:{location}"
            )
        return max(candidates, key=_version_sort_key)

    def download_package(self, package: RemotePackage, destination: Path) -> None:
        self._download(package.download_url, destination, accept=package.download_accept)

    def _api_url(self, path: str) -> str:
        return f"{self.config.api_base_url}{path}"

    def _list_package_files(self) -> list[dict[str, Any]]:
        encoded_path = quote(self.config.package_dir, safe="/")
        path = f"/repos/{self.config.owner}/{self.config.repo}/contents"
        if encoded_path:
            path += f"/{encoded_path}"
        url = self._api_url(f"{path}?ref={quote(self.config.branch, safe='')}")
        payload = self._request_json(url)
        if not isinstance(payload, list):
            raise RuntimeError(f"GitHub contents path is not a directory: {self.config.package_dir or '.'}")
        return payload

    def _is_package_candidate(self, item: dict[str, Any]) -> bool:
        return item.get("type") == "file" and fnmatch.fnmatch(item.get("name", ""), self.config.package_pattern)

    def _package_from_content_item(self, item: dict[str, Any]) -> RemotePackage:
        name = item.get("name", "")
        version = self._extract_version(name)
        download_url = item.get("url")
        sha = item.get("sha")
        path = item.get("path", name)
        if not download_url or not sha:
            raise RuntimeError(f"GitHub contents item for {name!r} is missing url or sha")
        return RemotePackage(
            version=version,
            download_url=download_url,
            branch=self.config.branch,
            path=path,
            sha=sha,
        )

    def _extract_version(self, file_name: str) -> str:
        match = re.fullmatch(self.config.version_regex, file_name)
        if not match:
            raise RuntimeError(f"Package file {file_name!r} did not match version_regex")
        if "version" in match.groupdict():
            return match.group("version")
        if match.groups():
            return match.group(1)
        raise RuntimeError("github.version_regex must define a named 'version' group or one capture group")

    def _request_json(self, url: str) -> Any:
        data = self._request_bytes(url, accept="application/vnd.github+json")
        return json.loads(data.decode("utf-8"))

    def _download(self, url: str, destination: Path, accept: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        LOG.info("Downloading %s to %s", url, destination)
        with tmp.open("wb") as f:
            data = self._request_bytes(url, accept=accept)
            f.write(data)
        tmp.replace(destination)

    def _request_bytes(self, url: str, accept: str) -> bytes:
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self.config.token}",
            "User-Agent": "python-runtime-agent/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        req = Request(url, headers=headers)
        last_error: Exception | None = None
        for attempt in range(1, 100):
            try:
                with urlopen(req, timeout=60,context=ssl_context) as response:
                    return response.read()
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise
            except OSError as exc:
                last_error = exc
            delay = min(2**attempt, 8)
            LOG.warning("GitHub request failed on attempt %s, retrying in %ss: %s", attempt, delay, last_error)
            time.sleep(delay)
        raise RuntimeError(f"GitHub request failed after retries: {last_error}")


def _version_sort_key(package: RemotePackage) -> VersionSortKey:
    return VersionSortKey(package.version)


@total_ordering
class VersionSortKey:
    def __init__(self, version: str) -> None:
        self.version = version

    def __lt__(self, other: "VersionSortKey") -> bool:
        return compare_versions(self.version, other.version) < 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, VersionSortKey) and compare_versions(self.version, other.version) == 0
