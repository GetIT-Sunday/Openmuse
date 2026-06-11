from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smearglepaper.github_tracker import GitHubTracker, _since_date
from smearglepaper.models import GitHubRepo


class GitHubTrackerTests(unittest.TestCase):
    def test_since_date_daily(self) -> None:
        result = _since_date("daily")
        expected = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(result, expected)

    def test_since_date_weekly(self) -> None:
        result = _since_date("weekly")
        expected = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%d")
        self.assertEqual(result, expected)

    def test_since_date_monthly(self) -> None:
        result = _since_date("monthly")
        expected = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%d")
        self.assertEqual(result, expected)

    def test_calculate_growth_no_previous(self) -> None:
        tracker = GitHubTracker()
        current = [
            GitHubRepo("a", "repo1", "a/repo1", "desc", 100, 10, "Python", "https://github.com/a/repo1", "2026-06-01T00:00:00+00:00"),
            GitHubRepo("b", "repo2", "b/repo2", "desc", 200, 20, "Rust", "https://github.com/b/repo2", "2026-06-01T00:00:00+00:00"),
        ]
        result = tracker.calculate_growth(current, None)
        self.assertEqual(result[0].full_name, "b/repo2")
        self.assertEqual(result[0].weekly_star_growth, None)

    def test_calculate_growth_with_previous(self) -> None:
        tracker = GitHubTracker()
        current = [
            GitHubRepo("a", "repo1", "a/repo1", "desc", 150, 10, "Python", "https://github.com/a/repo1", "2026-06-01T00:00:00+00:00"),
            GitHubRepo("b", "repo2", "b/repo2", "desc", 210, 20, "Rust", "https://github.com/b/repo2", "2026-06-01T00:00:00+00:00"),
        ]
        previous = [
            GitHubRepo("a", "repo1", "a/repo1", "desc", 100, 10, "Python", "https://github.com/a/repo1", "2026-05-25T00:00:00+00:00"),
            GitHubRepo("b", "repo2", "b/repo2", "desc", 200, 20, "Rust", "https://github.com/b/repo2", "2026-05-25T00:00:00+00:00"),
        ]
        result = tracker.calculate_growth(current, previous)
        self.assertEqual(result[0].full_name, "a/repo1")
        self.assertEqual(result[0].weekly_star_growth, 50)
        self.assertEqual(result[1].weekly_star_growth, 10)

    def test_calculate_growth_new_repo(self) -> None:
        tracker = GitHubTracker()
        current = [
            GitHubRepo("c", "repo3", "c/repo3", "desc", 50, 5, "Go", "https://github.com/c/repo3", "2026-06-01T00:00:00+00:00"),
        ]
        previous = [
            GitHubRepo("a", "repo1", "a/repo1", "desc", 100, 10, "Python", "https://github.com/a/repo1", "2026-05-25T00:00:00+00:00"),
        ]
        result = tracker.calculate_growth(current, previous)
        self.assertEqual(result[0].weekly_star_growth, None)

    def test_github_repo_to_from_dict(self) -> None:
        repo = GitHubRepo("owner", "name", "owner/name", "A test repo", 100, 10, "Python", "https://github.com/owner/name", "2026-06-01T00:00:00+00:00", 50)
        d = repo.to_dict()
        restored = GitHubRepo.from_dict(d)
        self.assertEqual(restored.full_name, "owner/name")
        self.assertEqual(restored.stars, 100)
        self.assertEqual(restored.weekly_star_growth, 50)

    def test_save_and_load_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch("smearglepaper.github_tracker.GITHUB_DIR", Path(tmp)):
                tracker = GitHubTracker()
                repos = [
                    GitHubRepo("a", "repo1", "a/repo1", "desc", 100, 10, "Python", "https://github.com/a/repo1", "2026-06-01T00:00:00+00:00"),
                ]
                path = tracker.save_snapshot(repos)
                self.assertTrue(path.exists())
                loaded = json.loads(path.read_text())
                self.assertEqual(loaded[0]["full_name"], "a/repo1")

    def test_collect_trending_mock(self) -> None:
        mock_response = json.dumps({
            "total_count": 1,
            "items": [
                {
                    "owner": {"login": "testuser"},
                    "name": "testrepo",
                    "full_name": "testuser/testrepo",
                    "description": "A test repo",
                    "stargazers_count": 500,
                    "forks_count": 50,
                    "language": "Python",
                    "html_url": "https://github.com/testuser/testrepo",
                }
            ],
        }).encode()

        with patch("smearglepaper.github_tracker._github_get") as mock_get:
            mock_get.return_value = json.loads(mock_response)
            tracker = GitHubTracker()
            repos = tracker.collect_trending(language="python", since="weekly", max_results=10)
            self.assertEqual(len(repos), 1)
            self.assertEqual(repos[0].full_name, "testuser/testrepo")
            self.assertEqual(repos[0].stars, 500)


if __name__ == "__main__":
    unittest.main()
