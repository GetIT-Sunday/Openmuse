from __future__ import annotations

import unittest

from smearglepaper.notifier import FeishuNotifier, _card_payload, _text_payload


class NotifierTests(unittest.TestCase):
    def test_dry_run_when_no_webhook(self) -> None:
        notifier = FeishuNotifier(webhook_url="", dry_run=True)
        result = notifier.notify("Test Title", "Test content")
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["title"], "Test Title")

    def test_dry_run_notify_papers(self) -> None:
        notifier = FeishuNotifier(dry_run=True)
        papers = [
            {"title": "Paper A", "url": "https://arxiv.org/abs/1234", "score": 5.5},
            {"title": "Paper B", "url": "https://arxiv.org/abs/5678", "score": 3.2},
        ]
        result = notifier.notify_papers("Test Papers", papers, top_k=2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("Paper A", result["content"])

    def test_dry_run_notify_blogs(self) -> None:
        notifier = FeishuNotifier(dry_run=True)
        posts = [
            {"title": "Blog A", "url": "https://example.com/a", "source": "OpenAI"},
            {"title": "Blog B", "url": "https://example.com/b", "source": "Anthropic"},
        ]
        result = notifier.notify_blogs("Test Blogs", posts, top_k=2)
        self.assertTrue(result["ok"])
        self.assertIn("Blog A", result["content"])
        self.assertIn("OpenAI", result["content"])

    def test_dry_run_notify_github_stars(self) -> None:
        notifier = FeishuNotifier(dry_run=True)
        repos = [
            {"full_name": "user/repo1", "url": "https://github.com/user/repo1", "stars": 1000, "weekly_star_growth": 50},
            {"full_name": "user/repo2", "url": "https://github.com/user/repo2", "stars": 500, "weekly_star_growth": 20},
        ]
        result = notifier.notify_github_stars("Test Stars", repos, top_k=2)
        self.assertTrue(result["ok"])
        self.assertIn("user/repo1", result["content"])
        self.assertIn("(+50)", result["content"])

    def test_dry_run_notify_github_stars_no_growth(self) -> None:
        notifier = FeishuNotifier(dry_run=True)
        repos = [
            {"full_name": "user/repo1", "url": "https://github.com/user/repo1", "stars": 1000, "weekly_star_growth": None},
        ]
        result = notifier.notify_github_stars("Test Stars", repos, top_k=1)
        self.assertTrue(result["ok"])
        self.assertIn("user/repo1", result["content"])
        self.assertNotIn("(+", result["content"])

    def test_card_payload_structure(self) -> None:
        payload = _card_payload("Title", "Content")
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["title"]["content"], "Title")
        self.assertEqual(payload["card"]["elements"][0]["content"], "Content")

    def test_text_payload_structure(self) -> None:
        payload = _text_payload("Hello")
        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["content"]["text"], "Hello")

    def test_dry_run_default_when_no_url(self) -> None:
        notifier = FeishuNotifier()
        self.assertTrue(notifier.dry_run)


if __name__ == "__main__":
    unittest.main()
