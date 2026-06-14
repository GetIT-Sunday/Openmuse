from __future__ import annotations

import json
import mimetypes
import re
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from .collector import ssl_context
from .config import env
from .models import Article

WECHAT_API = "https://api.weixin.qq.com/cgi-bin"


@dataclass
class DraftReport:
    ok: bool
    action: str
    media_id: str | None = None
    publish_id: str | None = None
    detail: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "action": self.action,
            "media_id": self.media_id,
            "publish_id": self.publish_id,
            "detail": self.detail or {},
        }


class WechatClient:
    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    def create_draft(self, article: Article) -> DraftReport:
        if self.dry_run:
            return DraftReport(True, "dry_run_create_draft", "dry_run_media_id", detail={"title": article.title, "figures": article.figure_paths}).to_dict()
        token = self._access_token()
        prepared = self.prepare_article_assets(article, token)
        payload = {"articles": [self._article_payload(prepared)]}
        data = self._post(f"{WECHAT_API}/draft/add?access_token={token}", payload)
        return DraftReport("media_id" in data, "create_draft", data.get("media_id"), detail=data).to_dict()

    def update_draft(self, article: Article, media_id: str, index: int = 0) -> DraftReport:
        if self.dry_run:
            return DraftReport(True, "dry_run_update_draft", media_id, detail={"index": index, "title": article.title}).to_dict()
        token = self._access_token()
        prepared = self.prepare_article_assets(article, token)
        payload = {"media_id": media_id, "index": index, "articles": self._article_payload(prepared)}
        data = self._post(f"{WECHAT_API}/draft/update?access_token={token}", payload)
        return DraftReport(data.get("errcode", 0) == 0, "update_draft", media_id, detail=data).to_dict()

    def prepare_article_assets(self, article: Article, token: str | None = None) -> Article:
        if self.dry_run:
            return article
        token = token or self._access_token()
        html = article.html
        for figure_path in article.figure_paths:
            path = Path(figure_path)
            if path.exists():
                image_url = self.upload_content_image(path, token)
                html = html.replace(str(path), image_url)
        thumb_media_id = article.thumb_media_id
        if not thumb_media_id and article.cover_path and Path(article.cover_path).exists():
            thumb_media_id = self.upload_thumb(Path(article.cover_path), token)
        return Article(
            paper=article.paper,
            title=article.title,
            digest=article.digest,
            markdown=article.markdown,
            html=html,
            cover_path=article.cover_path,
            figure_paths=article.figure_paths,
            thumb_media_id=thumb_media_id,
            word_count=article.word_count,
        )

    def upload_markdown_images(
        self,
        markdown: str,
        token: str | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Upload local Markdown images and replace their targets with WeChat URLs."""
        if self.dry_run:
            return markdown, {}
        token = token or self._access_token()
        replacements: dict[str, str] = {}
        for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
            raw_target = target.strip()
            path = Path(raw_target)
            if not _is_local_image_target(raw_target) or not path.exists():
                continue
            if raw_target not in replacements:
                replacements[raw_target] = self.upload_content_image(path, token)
        prepared = markdown
        for local_path, image_url in replacements.items():
            prepared = prepared.replace(f"]({local_path})", f"]({image_url})")
        return prepared, replacements

    def upload_thumb(self, path: Path, token: str | None = None) -> str:
        token = token or self._access_token()
        data = self._post_multipart(f"{WECHAT_API}/material/add_material?access_token={token}&type=thumb", "media", path)
        if "media_id" not in data:
            raise RuntimeError(f"WeChat thumb upload failed: {data}")
        return str(data["media_id"])

    def upload_content_image(self, path: Path, token: str | None = None) -> str:
        token = token or self._access_token()
        data = self._post_multipart(f"{WECHAT_API}/media/uploadimg?access_token={token}", "media", path)
        if "url" not in data:
            raise RuntimeError(f"WeChat content image upload failed: {data}")
        return str(data["url"])

    def publish_draft(self, media_id: str) -> str:
        token = self._access_token()
        data = self._post(f"{WECHAT_API}/freepublish/submit?access_token={token}", {"media_id": media_id})
        if "publish_id" not in data:
            raise RuntimeError(f"WeChat publish failed: {data}")
        return str(data["publish_id"])

    def get_publish_status(self, publish_id: str) -> dict[str, object]:
        token = self._access_token()
        return self._post(f"{WECHAT_API}/freepublish/get?access_token={token}", {"publish_id": publish_id})

    def _article_payload(self, article: Article) -> dict[str, object]:
        return {
            "title": article.title[:64],
            "thumb_media_id": article.thumb_media_id or "",
            "author": "SmearglePaper",
            "digest": article.digest[:120],
            "content": article.html,
            "content_source_url": article.paper.url,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }

    def _access_token(self) -> str:
        app_id = env("WECHAT_APP_ID")
        secret = env("WECHAT_APP_SECRET")
        if not app_id or not secret:
            raise RuntimeError("WECHAT_APP_ID and WECHAT_APP_SECRET are required for real WeChat calls.")
        query = urllib.parse.urlencode({"grant_type": "client_credential", "appid": app_id, "secret": secret})
        with urllib.request.urlopen(f"{WECHAT_API}/token?{query}", timeout=30, context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        if "access_token" not in data:
            raise RuntimeError(f"WeChat token failed: {data}")
        return str(data["access_token"])

    def _post(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_multipart(self, url: str, field_name: str, path: Path) -> dict[str, object]:
        boundary = f"----SmearglePaper{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                path.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))


def _is_local_image_target(target: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|/[^/]|file://)", target, flags=re.IGNORECASE))
