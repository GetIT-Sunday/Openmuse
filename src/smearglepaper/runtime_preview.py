from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import DATA_DIR, ROOT_DIR
from .preview_window import select_preview_html
from .runtime import AgentRuntime


class RuntimePreviewServer:
    def __init__(self, runtime: AgentRuntime, run_id: str) -> None:
        self.runtime = runtime
        self.run_id = run_id
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Preview server has not started.")
        return f"http://127.0.0.1:{self._server.server_port}/"

    def start(self) -> str:
        if self._server is not None:
            return self.url
        controller = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path == "/":
                    self._send(_preview_shell().encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/state":
                    self._send(json.dumps(controller.state(), ensure_ascii=False).encode("utf-8"), "application/json")
                elif path == "/article":
                    payload = controller.article_html()
                    if payload is None:
                        self.send_error(404, "Article not ready")
                    else:
                        self._send(payload.encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self.send_error(404, "Not found")

            def _send(self, payload: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name=f"preview-{self.run_id}")
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def state(self) -> dict[str, object]:
        manifest = self.runtime.get_run(self.run_id)
        source = select_preview_html(manifest.get("artifacts", []))
        version = _version(source) if source else ""
        request = dict(manifest.get("request", {}))
        inputs = dict(request.get("inputs", {}))
        return {
            "ready": source is not None,
            "version": version,
            "revision": int(inputs.get("revision_number", 1)),
            "status": manifest.get("status", "pending"),
        }

    def article_html(self) -> str | None:
        manifest = self.runtime.get_run(self.run_id)
        source = select_preview_html(manifest.get("artifacts", []))
        if source is None:
            return None
        allowed_roots = (self.runtime.workspace.resolve(), DATA_DIR.resolve(), ROOT_DIR.resolve())
        return _inline_local_images(source.read_text(encoding="utf-8"), source.parent, allowed_roots)


def _version(source: Path) -> str:
    digest = hashlib.sha256()
    digest.update(source.read_bytes())
    return digest.hexdigest()[:16]


def _inline_local_images(html: str, base_dir: Path, allowed_roots: tuple[Path, ...]) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix, raw, suffix = match.groups()
        if raw.startswith(("data:", "http://", "https://")):
            return match.group(0)
        candidate = Path(raw.removeprefix("file://")).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):
            return match.group(0)
        if not resolved.is_file() or not any(resolved == root or root in resolved.parents for root in allowed_roots):
            return f'{prefix}{suffix}'
        mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return f'{prefix}data:{mime};base64,{encoded}{suffix}'

    return re.sub(r'(<img\b[^>]*\bsrc=["\'])([^"\']*)(["\'])', replace, html, flags=re.IGNORECASE)


def _preview_shell() -> str:
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenMuse · AutoWechat Pack 手机预览</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef1f5;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
.bar{position:sticky;top:0;height:44px;padding:0 16px;background:#fff;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between;font-size:12px;color:#667085;z-index:2}
.brand{font-weight:700;color:#111827}.phone{width:390px;max-width:100%;min-height:calc(100vh - 44px);margin:0 auto;background:#fff}
iframe{display:block;width:100%;height:calc(100vh - 44px);border:0;background:#fff}
</style></head><body><div class="bar"><span class="brand">OpenMuse</span><span id="state">AutoWechat Pack · 等待文章</span></div>
<main class="phone"><iframe id="article" sandbox="" title="文章预览"></iframe></main>
<script>
let version=""; async function sync(){try{const s=await fetch('/state',{cache:'no-store'}).then(r=>r.json());
document.getElementById('state').textContent=s.ready?`第 ${s.revision} 版 · 已自动保存`:'正在生成文章';
if(s.ready&&s.version!==version){version=s.version;document.getElementById('article').src=`/article?v=${version}`;}}catch(e){document.getElementById('state').textContent='等待连接';}}
sync();setInterval(sync,1500);
</script></body></html>"""
