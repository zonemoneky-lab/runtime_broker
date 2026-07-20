from __future__ import annotations

import unittest
from typing import Any

from agent.config import GitHubConfig
from agent.github_client import GitHubClient


class BranchRuntimePackageTests(unittest.TestCase):
    def test_selects_latest_runtime_zip_from_branch_directory(self) -> None:
        client = FakeGitHubClient(
            GitHubConfig(
                owner="example",
                repo="runtime",
                token="token",
                branch="main",
                package_dir="packages",
            )
        )

        package = client.get_remote_package()

        self.assertEqual(package.branch, "main")
        self.assertEqual(package.version, "1.10.0")
        self.assertEqual(package.path, "packages/runtime-1.10.0.zip")
        self.assertEqual(package.sha, "sha-110")
        self.assertEqual(package.download_url, "https://api.github.com/repos/example/runtime/contents/packages/runtime-1.10.0.zip")
        self.assertEqual(package.download_accept, "application/vnd.github.raw")

    def test_reports_missing_runtime_zip(self) -> None:
        client = FakeGitHubClient(
            GitHubConfig(owner="example", repo="runtime", token="token", package_pattern="runtime-*.zip"),
            contents=[],
        )

        with self.assertRaisesRegex(RuntimeError, "No runtime package matched"):
            client.get_remote_package()


class FakeGitHubClient(GitHubClient):
    def __init__(self, config: GitHubConfig, contents: list[dict[str, str]] | None = None) -> None:
        super().__init__(config)
        self.contents = [
            {
                "type": "file",
                "name": "runtime-1.2.0.zip",
                "path": "packages/runtime-1.2.0.zip",
                "sha": "sha-120",
                "url": "https://api.github.com/repos/example/runtime/contents/packages/runtime-1.2.0.zip",
            },
            {
                "type": "file",
                "name": "runtime-1.10.0.zip",
                "path": "packages/runtime-1.10.0.zip",
                "sha": "sha-110",
                "url": "https://api.github.com/repos/example/runtime/contents/packages/runtime-1.10.0.zip",
            },
            {
                "type": "file",
                "name": "notes.txt",
                "path": "packages/notes.txt",
                "sha": "sha-notes",
                "url": "https://api.github.com/repos/example/runtime/contents/packages/notes.txt",
            },
        ] if contents is None else contents

    def _request_json(self, url: str) -> list[dict[str, Any]]:
        self.last_url = url
        return self.contents


if __name__ == "__main__":
    unittest.main()
