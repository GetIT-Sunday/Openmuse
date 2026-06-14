from __future__ import annotations

import html
import re


class WechatRenderer:
    def render(self, markdown: str) -> str:
        try:
            import markdown as md

            body = md.markdown(markdown, extensions=["extra", "sane_lists"])
        except ImportError:
            body = basic_markdown(markdown)
        body = inline_wechat_styles(body)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SmearglePaper Article</title>
  <style>
    body {{ font-family: "PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #000; line-height: 1.8; }}
    .smearglepaper {{ max-width: 680px; margin: 0 auto; padding: 24px 18px; }}
    h1 {{ font-size: 24px; line-height: 1.4; margin: 0 0 20px; }}
    h2 {{ font-size: 18px; border-left: 5px solid #916dd5; padding-left: 10px; margin-top: 30px; }}
    h3 {{ font-size: 17px; text-align: center; margin: 30px 0 15px; }}
    p {{ font-size: 14px; line-height: 1.8; letter-spacing: .02em; margin: 0; padding: 8px 0; }}
    blockquote {{ margin: 20px 0; padding: 10px 10px 10px 20px; background: #f4eeff; border-left: 3px solid #d89cf6; }}
    code {{ background: #f1f5f9; padding: 2px 4px; border-radius: 4px; }}
    strong {{ color: #916dd5; }}
    img {{ display: block; max-width: 100%; height: auto; margin: 10px auto; border-radius: 8px; box-shadow: rgba(153,153,153,.3) 2px 4px 8px; }}
    li {{ margin: 7px 0; }}
    table {{ width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 14px; }}
    th, td {{ border: 1px solid #dbe3ee; padding: 8px; text-align: left; }}
  </style>
</head>
<body><main class="smearglepaper">{body}</main></body>
</html>"""


def inline_wechat_styles(body: str) -> str:
    """Add inline styles that survive WeChat editor sanitization."""
    replacements = {
        "<h1>": '<h1 style="font-size:24px;line-height:1.4;margin:0 0 20px;color:#222;font-weight:bold;">',
        "<h2>": '<h2 style="display:block;font-size:18px;line-height:1.8;border-left:5px solid #916dd5;padding-left:10px;margin:30px 0 15px;color:#222;font-weight:bold;">',
        "<h3>": '<h3 style="font-size:17px;line-height:1.5;text-align:center;margin:30px 0 15px;color:#000;font-weight:bold;text-decoration:underline;text-decoration-color:#d89cf6;text-decoration-thickness:2px;text-underline-offset:5px;">',
        "<p>": '<p style="font-size:14px;line-height:1.8;letter-spacing:.02em;margin:0;padding:8px 0;color:#000;text-align:left;">',
        "<blockquote>": '<blockquote style="margin:20px 0;padding:10px 10px 10px 20px;background:#f4eeff;border-left:3px solid #d89cf6;color:#000;">',
        "<ul>": '<ul style="list-style-type:circle;padding-left:25px;margin:8px 0;color:#000;">',
        "<ol>": '<ol style="padding-left:25px;margin:8px 0;color:#000;">',
        "<li>": '<li style="font-size:14px;line-height:1.8;margin:5px 0;color:#010101;">',
        "<strong>": '<strong style="color:#916dd5;font-weight:bold;">',
        "<em>": '<em style="display:block;color:#888;font-size:13px;line-height:1.6;text-align:center;font-style:normal;margin:4px 0 14px;">',
        "<pre>": '<pre style="border-radius:5px;box-shadow:rgba(0,0,0,.35) 0 2px 10px;margin:10px 0;padding:0;overflow-x:auto;">',
        "<table>": '<table style="width:100%;border-collapse:collapse;margin:18px 0;font-size:14px;">',
        "<th>": '<th style="border:1px solid #dbe3ee;padding:8px;background:#f8fafc;text-align:left;">',
        "<td>": '<td style="border:1px solid #dbe3ee;padding:8px;text-align:left;">',
    }
    for source, target in replacements.items():
        body = body.replace(source, target)
    body = re.sub(
        r"<img ([^>]*?)>",
        r'<img \1 style="display:block;max-width:100%;height:auto;margin:10px auto;border-radius:8px;box-shadow:rgba(153,153,153,.3) 2px 4px 8px 0;">',
        body,
    )
    return body


def basic_markdown(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        escaped = html.escape(line)
        if escaped.startswith("# "):
            lines.append(f"<h1>{escaped[2:]}</h1>")
        elif escaped.startswith("## "):
            lines.append(f"<h2>{escaped[3:]}</h2>")
        elif escaped.startswith("- "):
            lines.append(f"<p>• {escaped[2:]}</p>")
        elif escaped.strip():
            lines.append(f"<p>{escaped}</p>")
    return re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", "\n".join(lines))
