from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smearglepaper.wechat import WechatClient


class WechatAssetTests(unittest.TestCase):
    def test_upload_markdown_images_deduplicates_and_replaces_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "figure.png"
            image.write_bytes(b"image")
            markdown = f"![A]({image})\n\n![B]({image})\n\n![Remote](https://example.com/a.png)"
            client = WechatClient(dry_run=False)
            with (
                patch.object(client, "_access_token", return_value="token"),
                patch.object(client, "upload_content_image", return_value="https://mmbiz.qpic.cn/figure.png") as upload,
            ):
                prepared, replacements = client.upload_markdown_images(markdown)

        upload.assert_called_once()
        self.assertEqual(len(replacements), 1)
        self.assertEqual(prepared.count("https://mmbiz.qpic.cn/figure.png"), 2)
        self.assertIn("https://example.com/a.png", prepared)


if __name__ == "__main__":
    unittest.main()
