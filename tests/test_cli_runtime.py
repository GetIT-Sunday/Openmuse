from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest

from smearglepaper.cli import main


class RuntimeCliTests(unittest.TestCase):
    def _invoke(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_mode_keeps_progress_off_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self._invoke(
                "run",
                "paper-to-article",
                "--offline-example",
                "--workspace",
                tmp,
                "--json",
            )
            payload = json.loads(stdout)
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(stderr, "")

    def test_human_mode_sends_events_to_stderr_and_result_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self._invoke(
                "run",
                "paper-research",
                "--offline-example",
                "--workspace",
                tmp,
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["status"], "completed")
            self.assertIn("[run.started]", stderr)
            self.assertIn("[step.completed]", stderr)

    def test_real_publish_waits_for_approval_with_exit_code_seven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = self._invoke(
                "run",
                "paper-to-wechat",
                "--offline-example",
                "--real",
                "--workspace",
                tmp,
                "--json",
            )
            payload = json.loads(stdout)
            self.assertEqual(code, 7)
            self.assertEqual(payload["status"], "waiting_approval")
            self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
