from __future__ import annotations

import html
import json
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlparse

from .artifact_integrity import article_bundle_fingerprint
from .models import Article
from .preview import preview_bundle_fingerprint
from .product_tasks import ProductTask, ProductTaskRepository
from .storage import read_json


def preview_urls(task_id: str, *, host: str = "127.0.0.1", port: int = 8765) -> dict[str, str]:
    base = f"http://{host}:{port}/tasks/{quote(task_id)}"
    return {
        "preview": f"{base}/preview",
        "check": f"{base}/check",
        "readiness_api": f"http://{host}:{port}/api/tasks/{quote(task_id)}/readiness",
    }


def lan_preview_urls(task_id: str, *, port: int = 8765) -> dict[str, str] | None:
    host = _lan_ip()
    return preview_urls(task_id, host=host, port=port) if host else None


def build_readiness(task: ProductTask) -> dict[str, object]:
    article_json = _artifact_path(task.artifacts.get("final_article_json"))
    article = Article.from_dict(read_json(article_json, {})) if article_json.exists() else None
    preview_manifest = _artifact_path(task.artifacts.get("preview_manifest"))
    preview_payload = read_json(preview_manifest, {}) if preview_manifest.exists() else {}
    technical = dict(task.artifacts.get("technical_review", {})) if isinstance(task.artifacts.get("technical_review"), dict) else {}
    wechat = dict(task.artifacts.get("wechat_review", {})) if isinstance(task.artifacts.get("wechat_review"), dict) else {}
    article_fingerprint = article_bundle_fingerprint(article_json) if article_json.exists() else None
    stored_article_fingerprint = task.artifacts.get("article_fingerprint")
    preview_fingerprint = preview_bundle_fingerprint(preview_manifest) if preview_manifest.exists() else None
    generated_preview_fingerprint = preview_payload.get("fingerprint") if isinstance(preview_payload, dict) else None
    stored_preview_fingerprint = task.artifacts.get("preview_fingerprint")
    approval = task.latest_approval("publish")
    image_state = _image_state(article)
    mobile_html = _artifact_path(preview_payload.get("mobile_html"))
    mobile_ready = bool(preview_payload.get("mobile_preview_ready") or mobile_html.exists())

    article_fresh = bool(article_fingerprint and article_fingerprint == stored_article_fingerprint)
    preview_fresh = bool(generated_preview_fingerprint and generated_preview_fingerprint == stored_preview_fingerprint)
    publish_ready = bool(task.artifacts.get("publish_ready"))
    approval_bound = bool(
        approval
        and dict(approval.get("detail", {})).get("fingerprint") == article_fingerprint
        and dict(approval.get("detail", {})).get("preview_fingerprint") == preview_fingerprint
    )
    can_approve_publish = (
        task.status == "awaiting_publish_approval"
        and publish_ready
        and article_fresh
        and preview_fresh
        and mobile_ready
        and not image_state["local_sources"]
    )
    can_create_draft = (
        task.status == "creating_draft"
        and publish_ready
        and article_fresh
        and preview_fresh
        and approval_bound
        and not image_state["local_sources"]
    )
    state, label = _result_state(task, can_approve_publish=can_approve_publish, can_create_draft=can_create_draft)

    blockers: list[str] = []
    if not article_json.exists():
        blockers.append("没有找到最终文章 JSON。")
    if not publish_ready:
        blockers.append("文章尚未达到 publish_ready。")
    if not preview_manifest.exists():
        blockers.append("没有生成预览清单。")
    if not article_fresh:
        blockers.append("文章内容已变化，需要重新 review 或重新生成预览。")
    if not preview_fresh:
        blockers.append("预览内容已变化，需要重新生成预览。")
    if image_state["local_sources"]:
        blockers.append(f"仍有 {len(image_state['local_sources'])} 个本地图片路径，需要先上传到微信。")
    if task.status not in {"awaiting_publish_approval", "creating_draft", "draft_simulated", "draft_created"}:
        blockers.append(f"任务当前状态是 {task.status}，还没有进入发布确认阶段。")

    return {
        "task": {
            "task_id": task.task_id,
            "intent": task.intent,
            "status": task.status,
            "active_stage": task.active_stage,
            "updated_at": task.metrics.get("updated_at"),
        },
        "article": {
            "title": article.title if article else "",
            "digest": article.digest if article else "",
            "paper_id": article.paper.paper_id if article else "",
            "paper_title": article.paper.title if article else "",
            "paper_url": article.paper.url if article else "",
            "json": str(article_json) if article_json else "",
            "word_count": article.word_count if article else 0,
            "fingerprint_fresh": article_fresh,
        },
        "quality": {
            "content_ready": bool(task.artifacts.get("content_ready")),
            "publish_ready": publish_ready,
            "technical_score": technical.get("score"),
            "wechat_score": wechat.get("score"),
            "technical_blockers": technical.get("blocking_issues", []),
            "wechat_blockers": wechat.get("publication_blocking_issues", []),
        },
        "images": image_state,
        "preview": {
            "manifest": str(preview_manifest) if preview_manifest else "",
            "mobile_html": str(preview_payload.get("mobile_html", "")),
            "desktop_html": str(preview_payload.get("desktop_html", "")),
            "article_html": str(preview_payload.get("article_html", "")),
            "mobile_ready": mobile_ready,
            "screenshot_ready": bool(preview_payload.get("screenshot_ready")),
            "screenshot_errors": preview_payload.get("screenshot_errors", []),
            "fingerprint_fresh": preview_fresh,
        },
        "approval": {
            "approved": bool(approval),
            "approved_at": approval.get("approved_at") if approval else None,
            "bound_to_current_preview": approval_bound,
            "next_cli": f"smearglepaper task-approve --task-id {task.task_id} --gate publish"
            if can_approve_publish
            else f"smearglepaper task-resume --task-id {task.task_id} --real-wechat"
            if can_create_draft
            else "",
        },
        "wechat": task.wechat,
        "result": {
            "state": state,
            "label": label,
            "blockers": blockers,
            "can_approve_publish": can_approve_publish,
            "can_create_draft": can_create_draft,
        },
    }


def serve_task_preview(
    task_id: str,
    *,
    repository: ProductTaskRepository | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    ready_callback: Callable[[dict[str, object]], None] | None = None,
) -> None:
    repository = repository or ProductTaskRepository()
    handler = _handler(repository, task_id)
    server = ThreadingHTTPServer((host, port), handler)
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    local = preview_urls(task_id, host=display_host, port=server.server_port)
    lan = lan_preview_urls(task_id, port=server.server_port) if display_host != host or not _is_loopback_host(host) else None
    if ready_callback:
        ready_callback({"local": local, "lan": lan})
    try:
        server.serve_forever()
    finally:
        server.server_close()


def render_preview_page(task: ProductTask, readiness: dict[str, object]) -> str:
    article = dict(readiness.get("article", {}))
    result = dict(readiness.get("result", {}))
    badge = "Ready" if result.get("state") == "ready" else "Review"
    return _page(
        title=f"{article.get('title') or task.task_id} - 移动端预览",
        body=f"""
        <main class="screen preview-screen">
          {_hero(task, readiness, active="preview")}
          <section class="phone-card">
            <div class="dynamic-island"></div>
            <iframe class="article-frame" src="/tasks/{html.escape(task.task_id)}/article" title="article preview"></iframe>
          </section>
          <section class="bottom-card">
            <div>
              <p class="eyebrow">{badge}</p>
              <h2>{html.escape(str(result.get("label") or ""))}</h2>
            </div>
            <a class="button" href="/tasks/{html.escape(task.task_id)}/check">查看发布前检查</a>
          </section>
        </main>
        """,
    )


def render_check_page(task: ProductTask, readiness: dict[str, object]) -> str:
    result = dict(readiness.get("result", {}))
    article = dict(readiness.get("article", {}))
    quality = dict(readiness.get("quality", {}))
    images = dict(readiness.get("images", {}))
    preview = dict(readiness.get("preview", {}))
    approval = dict(readiness.get("approval", {}))
    blockers = list(result.get("blockers", []))
    return _page(
        title=f"{article.get('title') or task.task_id} - 发布前检查",
        body=f"""
        <main class="screen check-screen">
          {_hero(task, readiness, active="check")}
          <section class="verdict {html.escape(str(result.get("state") or ""))}">
            <span>{html.escape(str(result.get("label") or ""))}</span>
            <strong>{'所有关键检查已通过' if not blockers else f'{len(blockers)} 个项目需要处理'}</strong>
          </section>
          {_card("文章", [
              _row("标题", str(article.get("title") or "未生成")),
              _row("论文", str(article.get("paper_id") or "未知")),
              _row("来源", _link(str(article.get("paper_url") or ""))),
              _row("文章指纹", _ok_text(bool(article.get("fingerprint_fresh")))),
          ])}
          {_card("质量", [
              _row("技术审查", _score(quality.get("technical_score"))),
              _row("公众号审查", _score(quality.get("wechat_score"))),
              _row("content_ready", _ok_text(bool(quality.get("content_ready")))),
              _row("publish_ready", _ok_text(bool(quality.get("publish_ready")))),
          ])}
          {_card("图片", [
              _row("图片总数", str(images.get("total", 0))),
              _row("微信图片", str(images.get("wechat", 0))),
              _row("远程图片", str(images.get("remote", 0))),
              _row("本地路径", str(len(list(images.get("local_sources", []))))),
          ])}
          {_card("预览", [
              _row("移动端 HTML", _ok_text(bool(preview.get("mobile_ready")))),
              _row("截图", "已生成" if preview.get("screenshot_ready") else "HTML 可用，截图未生成"),
              _row("预览指纹", _ok_text(bool(preview.get("fingerprint_fresh")))),
          ])}
          {_card("审批", [
              _row("已有审批", _ok_text(bool(approval.get("approved")))),
              _row("绑定当前预览", _ok_text(bool(approval.get("bound_to_current_preview")))),
              _row("下一步命令", f"<code>{html.escape(str(approval.get('next_cli') or '先处理上方问题'))}</code>"),
          ])}
          {_blockers(blockers)}
        </main>
        """,
    )


def _handler(repository: ProductTaskRepository, default_task_id: str) -> type[BaseHTTPRequestHandler]:
    class PreviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/":
                self._redirect(f"/tasks/{quote(default_task_id)}/preview")
                return
            match = re.match(r"^/api/tasks/([^/]+)/readiness$", path)
            if match:
                task = repository.load(match.group(1))
                self._json(build_readiness(task))
                return
            match = re.match(r"^/tasks/([^/]+)/(preview|check|article)$", path)
            if match:
                task = repository.load(match.group(1))
                readiness = build_readiness(task)
                page = match.group(2)
                if page == "article":
                    self._article(task)
                elif page == "check":
                    self._html(render_check_page(task, readiness))
                else:
                    self._html(render_preview_page(task, readiness))
                return
            self.send_error(404, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _article(self, task: ProductTask) -> None:
            preview_manifest = _artifact_path(task.artifacts.get("preview_manifest"))
            preview = read_json(preview_manifest, {}) if preview_manifest.exists() else {}
            article_html = Path(str(preview.get("article_html") or ""))
            if not article_html.exists():
                self.send_error(404, "Article preview not found")
                return
            self._bytes(article_html.read_bytes(), "text/html; charset=utf-8")

        def _html(self, payload: str) -> None:
            self._bytes(payload.encode("utf-8"), "text/html; charset=utf-8")

        def _json(self, payload: object) -> None:
            self._bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8")

        def _redirect(self, target: str) -> None:
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def _bytes(self, payload: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return PreviewHandler


def _image_state(article: Article | None) -> dict[str, object]:
    sources: list[str] = []
    if article:
        sources.extend(_html_images(article.html))
    unique = list(dict.fromkeys(source for source in sources if source))
    local = [source for source in unique if _is_local_image_source(source)]
    wechat = [source for source in unique if "mmbiz.qpic.cn" in source or "mmbiz.qlogo.cn" in source]
    remote = [source for source in unique if source.startswith(("http://", "https://"))]
    return {
        "total": len(unique),
        "wechat": len(wechat),
        "remote": len(remote),
        "local_sources": local,
        "sources": unique[:30],
        "source_paths": article.figure_paths[:30] if article else [],
        "cover_path": article.cover_path if article else None,
        "cover_thumb_ready": bool(not article or not article.cover_path or article.thumb_media_id or Path(article.cover_path).exists()),
    }


def _artifact_path(value: object) -> Path:
    text = str(value or "")
    return Path(text) if text else Path("__missing_artifact__")


def _result_state(task: ProductTask, *, can_approve_publish: bool, can_create_draft: bool) -> tuple[str, str]:
    if can_approve_publish:
        return "ready", "可以创建公众号草稿"
    if can_create_draft:
        return "approved", "已确认，等待创建草稿"
    if task.status == "draft_simulated":
        return "simulated", "本地草稿模拟已完成"
    if task.status == "draft_created":
        return "done", "公众号草稿已创建"
    return "needs_attention", "暂不能发布"


def _html_images(value: str) -> list[str]:
    return re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", value or "", flags=re.IGNORECASE)


def _is_local_image_source(source: str) -> bool:
    if source.startswith(("http://", "https://", "data:")):
        return False
    return bool(source and (source.startswith(("file:", "/", "./", "../")) or re.match(r"^[A-Za-z]:[\\/]", source) or not re.match(r"^[a-z]+:", source)))


def _page(*, title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{html.escape(title)}</title>
  <style>{_css()}</style>
</head>
<body>
  {body}
</body>
</html>"""


def _hero(task: ProductTask, readiness: dict[str, object], *, active: str) -> str:
    article = dict(readiness.get("article", {}))
    result = dict(readiness.get("result", {}))
    return f"""
    <header class="hero">
      <div class="status-pill">{html.escape(str(result.get("label") or ""))}</div>
      <h1>{html.escape(str(article.get("title") or task.task_id))}</h1>
      <p>{html.escape(str(article.get("paper_title") or article.get("paper_id") or "移动端发布工作台"))}</p>
      <nav class="tabs">
        <a class="{'active' if active == 'preview' else ''}" href="/tasks/{html.escape(task.task_id)}/preview">正文预览</a>
        <a class="{'active' if active == 'check' else ''}" href="/tasks/{html.escape(task.task_id)}/check">发布检查</a>
      </nav>
    </header>
    """


def _card(title: str, rows: list[str]) -> str:
    return f'<section class="card"><h2>{html.escape(title)}</h2>{"".join(rows)}</section>'


def _row(label: str, value: str) -> str:
    return f'<div class="row"><span>{html.escape(label)}</span><strong>{value}</strong></div>'


def _link(value: str) -> str:
    if not value:
        return "无"
    return f'<a href="{html.escape(value)}">{html.escape(value)}</a>'


def _score(value: object) -> str:
    return "未评估" if value is None else f"{html.escape(str(value))} / 100"


def _ok_text(value: bool) -> str:
    return '<span class="ok">通过</span>' if value else '<span class="bad">未通过</span>'


def _blockers(blockers: list[str]) -> str:
    if not blockers:
        return '<section class="card calm"><h2>结论</h2><p>可以回到 CLI 执行发布审批命令，然后创建公众号草稿。</p></section>'
    items = "".join(f"<li>{html.escape(item)}</li>" for item in blockers)
    return f'<section class="card warning"><h2>需要处理</h2><ul>{items}</ul></section>'


def _css() -> str:
    return """
    :root { color-scheme: light; --bg:#f5f5f7; --card:rgba(255,255,255,.82); --ink:#1d1d1f; --muted:#6e6e73; --line:rgba(60,60,67,.13); --blue:#007aff; --green:#34c759; --red:#ff3b30; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background: radial-gradient(circle at 20% -10%, #dbeafe 0, transparent 36%), radial-gradient(circle at 95% 0%, #fce7f3 0, transparent 34%), var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI","PingFang SC",sans-serif; }
    a { color:var(--blue); text-decoration:none; }
    .screen { width:min(100%, 480px); margin:0 auto; padding: max(18px, env(safe-area-inset-top)) 14px 34px; }
    .hero { position:sticky; top:0; z-index:2; padding:14px 0 10px; backdrop-filter: blur(22px); -webkit-backdrop-filter: blur(22px); }
    .status-pill { display:inline-flex; padding:7px 11px; border-radius:999px; background:rgba(0,122,255,.12); color:#0057b8; font-size:12px; font-weight:700; }
    h1 { margin:12px 0 8px; font-size:28px; line-height:1.14; letter-spacing:-.03em; }
    .hero p { margin:0; color:var(--muted); font-size:14px; line-height:1.45; }
    .tabs { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:16px; padding:4px; border-radius:16px; background:rgba(118,118,128,.12); }
    .tabs a { text-align:center; color:var(--ink); padding:9px 8px; border-radius:12px; font-size:14px; font-weight:650; }
    .tabs a.active { background:#fff; box-shadow:0 2px 10px rgba(0,0,0,.08); }
    .phone-card, .card, .bottom-card, .verdict { background:var(--card); border:1px solid rgba(255,255,255,.72); box-shadow:0 18px 50px rgba(0,0,0,.10); backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px); }
    .phone-card { margin-top:14px; border-radius:38px; padding:14px 10px 12px; min-height:72vh; overflow:hidden; }
    .dynamic-island { width:86px; height:25px; margin:0 auto 10px; border-radius:999px; background:#111; }
    .article-frame { width:100%; min-height:72vh; border:0; border-radius:24px; background:#fff; }
    .bottom-card { display:flex; align-items:center; justify-content:space-between; gap:14px; margin-top:14px; border-radius:24px; padding:16px; }
    .eyebrow { margin:0 0 4px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
    .bottom-card h2, .card h2 { margin:0; font-size:18px; letter-spacing:-.02em; }
    .button { flex:none; border-radius:999px; padding:11px 14px; color:#fff; background:var(--blue); font-weight:700; font-size:14px; }
    .verdict { margin-top:14px; border-radius:26px; padding:18px; }
    .verdict span { display:block; color:var(--muted); font-size:13px; margin-bottom:4px; }
    .verdict strong { font-size:22px; letter-spacing:-.03em; }
    .verdict.ready { background:rgba(236,253,245,.86); }
    .card { margin-top:12px; border-radius:26px; padding:17px; }
    .row { display:flex; justify-content:space-between; gap:18px; padding:12px 0; border-top:1px solid var(--line); color:var(--muted); font-size:14px; }
    .row:first-of-type { border-top:0; }
    .row strong { text-align:right; color:var(--ink); font-weight:650; overflow-wrap:anywhere; }
    .ok { color:var(--green); }
    .bad { color:var(--red); }
    .warning { background:rgba(255,244,230,.9); }
    .calm { background:rgba(236,253,245,.9); }
    code { display:inline-block; padding:6px 8px; border-radius:10px; background:rgba(118,118,128,.12); font-family:"SF Mono",Consolas,monospace; font-size:12px; }
    ul { margin:10px 0 0; padding-left:20px; color:#7c2d12; line-height:1.55; }
    @media (min-width: 900px) { .screen { width:min(100%, 1060px); } .preview-screen { display:grid; grid-template-columns:430px 1fr; gap:24px; align-items:start; } .preview-screen .hero { grid-column:1 / -1; } .phone-card { width:430px; } .bottom-card { margin-top:14px; align-self:start; } .check-screen { width:min(100%, 760px); } }
    """


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            return host if not host.startswith("127.") else None
    except OSError:
        return None


def _is_loopback_host(host: str) -> bool:
    return host in {"localhost", "::1"} or host.startswith("127.")


__all__ = [
    "build_readiness",
    "lan_preview_urls",
    "preview_urls",
    "render_check_page",
    "render_preview_page",
    "serve_task_preview",
]
