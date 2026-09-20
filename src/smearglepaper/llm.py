from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from typing import Any
from urllib.error import HTTPError

from .collector import ssl_context
from .config import env
from .evidence import format_evidence_context
from .models import PaperMeta

DEFAULT_LLM_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 "
    "SmearglePaper/0.2"
)
_PROCESS_SESSION_ID = f"autowechat-{uuid.uuid4().hex}"


def openai_request_headers(
    api_key: str | None = None,
    session_id: str | None = None,
    base_url: str | None = None,
) -> dict[str, str]:
    """Headers for OpenAI-compatible gateways, including bot-filter compatibility."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": env("LLM_USER_AGENT", DEFAULT_LLM_USER_AGENT),
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    endpoint = (base_url or env("OPENAI_BASE_URL")).lower()
    if "opencode.ai/zen/go" in endpoint:
        headers["x-opencode-session"] = session_id or env("OPENAI_SESSION_ID", _PROCESS_SESSION_ID)
    return headers


class ArticleWriter:
    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.usage: dict[str, int | float | None] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": None,
        }

    def reset_usage(self) -> None:
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cost": None}

    def write(self, paper: PaperMeta, parsed: dict[str, object] | None = None) -> str:
        context = build_context(paper, parsed)
        if _detect_api_provider() != "none":
            try:
                notes = self._call_model("你是严谨的论文阅读助手，只输出结构化中文要点。", notes_prompt(paper, context), temperature=0.2)
                outline = self._call_model("你是科技文章编辑，只输出清晰的文章大纲。", outline_prompt(paper, notes), temperature=0.3)
                return self._call_model("你是严谨的中文科技作者，输出适合微信公众号的 Markdown 深度解读。", article_prompt(paper, notes, outline), temperature=0.45)
            except Exception as exc:  # noqa: BLE001 - local fallback remains available
                print(
                    f"Warning: LLM article generation failed; using local template: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        return self._write_locally(paper, context)

    def write_local(self, paper: PaperMeta, parsed: dict[str, object] | None = None) -> str:
        """Generate the deterministic local draft without contacting a provider."""
        return self._write_locally(paper, build_context(paper, parsed))

    def _call_model(self, system: str, user: str, temperature: float) -> str:
        provider = _detect_api_provider()
        if provider == "anthropic":
            return self._call_anthropic(system, user, temperature)
        return self._call_openai(system, user, temperature)

    def _call_openai(self, system: str, user: str, temperature: float) -> str:
        base = chat_completions_base_url(env("OPENAI_BASE_URL"))
        max_tokens = int(env("LLM_MAX_TOKENS", "8192"))
        payload = {
            "model": self.model or env("OPENAI_MODEL", "deepseek-chat"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=openai_request_headers(env("OPENAI_API_KEY"), base_url=base),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_llm_timeout(), context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        self._record_usage(data.get("usage", {}), "prompt_tokens", "completion_tokens")
        return data["choices"][0]["message"]["content"]

    def _call_anthropic(self, system: str, user: str, temperature: float) -> str:
        base_url = env("ANTHROPIC_BASE_URL").rstrip("/")
        payload = {
            "model": self.model or env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            "max_tokens": int(env("LLM_MAX_TOKENS", "8192")),
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        req = urllib.request.Request(
            f"{base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": env("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_llm_timeout(), context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        self._record_usage(data.get("usage", {}), "input_tokens", "output_tokens")
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        return ""

    def _record_usage(self, usage: object, input_key: str, output_key: str) -> None:
        if not isinstance(usage, dict):
            return
        self.usage["input_tokens"] = int(self.usage.get("input_tokens") or 0) + int(usage.get(input_key, 0) or 0)
        self.usage["output_tokens"] = int(self.usage.get("output_tokens") or 0) + int(usage.get(output_key, 0) or 0)

    def _write_locally(self, paper: PaperMeta, context: str) -> str:
        authors = "、".join(paper.authors[:5]) or "作者未列出"
        categories = "、".join(paper.categories) or "未标注"
        abstract = paper.abstract or "当前只提供了论文链接，尚未抓取到摘要。可以先运行 read 命令解析 PDF，再生成更完整的版本。"
        method_hint = _sentence(context, ["propose", "introduce", "method", "framework", "approach"])
        experiment_hint = _sentence(context, ["experiment", "benchmark", "dataset", "outperform", "improve"])
        return f"""# {paper.title}

> 这是一篇由 SmearglePaper 多阶段本地流程生成的论文解读草稿。配置 OpenAI 兼容模型后，会自动执行“阅读笔记 -> 大纲 -> 成文”的三段式写作。

## 一句话结论

这篇论文围绕 **{paper.title}** 展开，值得关注的原因在于它切中了近期 AI 研究中“能力提升、系统可用性与评测可信度”之间的张力。

## 论文信息

- 论文编号：{paper.paper_id}
- 作者：{authors}
- 来源：{paper.source}
- 类别：{categories}
- 链接：{paper.url}

## 研究问题

作者试图回答的问题可以概括为：在当前模型与数据条件下，怎样让系统在目标任务上表现得更强、更稳定，或者更容易被实际使用。

## 方法概览

从摘要看，论文的核心贡献可以拆成三层：第一，提出或整理了一个明确的问题设定；第二，给出面向该问题的模型、训练、推理或评测方案；第三，通过实验比较说明该方案相对已有方法的优势与边界。

{method_hint}

## 关键发现

{abstract}

{experiment_hint}

## 图表线索

如果 `read` 命令成功抽取 PDF 图表，系统会把候选图片写入 `data/figures/`，后续创建真实微信草稿时可自动上传正文图片。

## 为什么重要

如果这项工作能被复现，它可能对后续研究产生两类影响：一是提供新的实验基线，二是把一个原本分散的技术问题收束成更清晰的工程流程。

## 局限与风险

- 仅凭摘要无法确认全部实验细节，关键结论需要回到论文正文和消融实验。
- 如果数据集、提示词或评测脚本没有公开，复现可信度会下降。
- 对公众号读者来说，最需要警惕的是把单一 benchmark 的提升误读成通用能力跃迁。

## 适合谁读与继续追问

- 实验设置是否覆盖真实使用场景？
- 数据集或评测指标是否存在偏差？
- 方法收益来自新算法，还是来自更强的数据与调参？
- 代码、模型或数据是否足以支持独立复现？
"""


def build_context(paper: PaperMeta, parsed: dict[str, object] | None = None) -> str:
    chunks = [paper.abstract]
    if parsed:
        chunks.append(format_evidence_context(parsed))
        chunks.append(format_visual_evidence(parsed))
        chunks.append(str(parsed.get("text", ""))[:16000])
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()


def format_visual_evidence(parsed: dict[str, object]) -> str:
    visuals = parsed.get("visuals", [])
    if not isinstance(visuals, list) or not visuals:
        return ""
    lines = ["PDF 图表证据索引（写作时应按标题和页码解释，不要只把图片当装饰）："]
    for item in visuals:
        if not isinstance(item, dict):
            continue
        kind = "表格" if item.get("kind") == "table" else "图"
        caption = str(item.get("caption", "")).strip() or "未识别到标题"
        lines.append(f"- {item.get('id', kind)}｜第 {item.get('page', '?')} 页｜{caption}")
    return "\n".join(lines)


def chat_completions_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def check_llm_connection() -> dict[str, object]:
    provider = _detect_api_provider()
    if provider == "anthropic":
        return _check_anthropic()
    elif provider == "openai":
        return _check_openai()
    else:
        return {"ok": False, "error": "No LLM configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."}


def _check_openai() -> dict[str, object]:
    return check_openai_connection(env("OPENAI_BASE_URL"), env("OPENAI_API_KEY"), env("OPENAI_MODEL", "deepseek-chat"))


def check_openai_connection(
    base_url: str,
    api_key: str | None,
    model: str,
    session_id: str | None = None,
) -> dict[str, object]:
    """Check one OpenAI-compatible connection without changing global configuration."""
    base_url = base_url.strip()
    api_key = (api_key or "").strip()
    model = model.strip() or "deepseek-chat"
    payload = {"model": model, "messages": [{"role": "user", "content": "Return OK only."}], "max_tokens": 64}
    req = urllib.request.Request(
        f"{chat_completions_base_url(base_url)}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=openai_request_headers(api_key, session_id=session_id, base_url=base_url),
        method="POST",
    )
    try:
        # Connection checks should fail quickly; a stalled gateway must not
        # make the whole TUI feel frozen.
        with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        message = data.get("choices", [{}])[0].get("message", {})
        sample = message.get("content") or message.get("reasoning") or ""
        return {"ok": True, "provider": "openai", "model": model, "status": "connected", "sample": sample[:80]}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "provider": "openai",
            "model": model,
            "status_code": exc.code,
            "error": _connection_error_detail(body),
        }
    except Exception as exc:
        return {"ok": False, "provider": "openai", "model": model, "error": f"{type(exc).__name__}: {exc}"}


def _connection_error_detail(body: str) -> str:
    """Extract a short, non-secret provider error suitable for the TUI."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            for key in ("message", "detail", "type", "code"):
                value = error.get(key)
                if value:
                    return str(value)[:280]
        if isinstance(error, str):
            return error[:280]
    compact = " ".join(body.split())
    return compact[:280] or "服务端没有返回错误说明"


def list_openai_models(base_url: str, api_key: str) -> list[str]:
    """Return model ids from an OpenAI-compatible gateway."""
    req = urllib.request.Request(
        f"{chat_completions_base_url(base_url.strip())}/models",
        headers=openai_request_headers(api_key.strip(), base_url=base_url),
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as response:
        data = json.loads(response.read().decode("utf-8"))
    models = data.get("data", []) if isinstance(data, dict) else []
    result = {
        str(item.get("id", "")).strip()
        for item in models
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    return sorted(result)


def _check_anthropic() -> dict[str, object]:
    base_url = env("ANTHROPIC_BASE_URL").rstrip("/")
    api_key = env("ANTHROPIC_API_KEY")
    model = env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    payload = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Say OK"}]}
    req = urllib.request.Request(
        f"{base_url}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        return {"ok": True, "provider": "anthropic", "model": model, "status": "connected", "sample": text[:80]}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "provider": "anthropic", "model": model, "status_code": exc.code, "error": body[:500]}
    except Exception as exc:
        return {"ok": False, "provider": "anthropic", "model": model, "error": f"{type(exc).__name__}: {exc}"}


def notes_prompt(paper: PaperMeta, context: str) -> str:
    return f"""请基于以下论文元数据，写一篇中文微信公众号深度解读。

要求：
1. 提取研究问题、核心方法、实验设置、关键结果、局限性；
2. 区分论文明确声称与推断；
3. 不要编造论文中没有的信息。

标题：{paper.title}
作者：{", ".join(paper.authors)}
摘要：{paper.abstract}
分类：{", ".join(paper.categories)}
链接：{paper.url}
正文片段：{context[:12000]}
"""


def outline_prompt(paper: PaperMeta, notes: str) -> str:
    return f"""请把论文阅读笔记改写成微信公众号文章大纲。

标题：{paper.title}
阅读笔记：
{notes}
"""


def article_prompt(paper: PaperMeta, notes: str, outline: str) -> str:
    return f"""请根据阅读笔记和大纲，写一篇中文微信公众号 Markdown 深度解读。

要求：
1. 标题克制，不夸张；
2. 包含：一句话结论、研究问题、方法概览、关键实验、局限性、适合谁读；
3. 保留论文链接；
4. 不编造实验数字；
5. 语言清楚，有技术密度但适合研究生和工程师阅读。

论文标题：{paper.title}
论文链接：{paper.url}
阅读笔记：
{notes}

文章大纲：
{outline}
"""


def _detect_api_provider() -> str:
    """Detect which API provider to use based on env vars."""
    if env("ANTHROPIC_API_KEY") and env("ANTHROPIC_BASE_URL"):
        return "anthropic"
    if env("OPENAI_API_KEY") and env("OPENAI_BASE_URL"):
        return "openai"
    return "none"


def _llm_timeout(default: int = 60) -> int:
    """Bound provider waits so a stalled request cannot hold a run for minutes."""
    try:
        configured = int(env("LLM_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        configured = default
    return max(5, min(configured, 300))


def chat_with_tools(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict[str, object]:
    """Call an LLM with function calling support (auto-detects Anthropic/OpenAI).

    Returns a dict with:
      - content: str | None  (final text reply, if any)
      - tool_calls: list[dict]  (tool calls requested by the LLM, if any)
      - finish_reason: str
    """
    provider = _detect_api_provider()
    if provider == "anthropic":
        return _chat_anthropic(messages, tools, model, temperature, max_tokens)
    elif provider == "openai":
        return _chat_openai(messages, tools, model, temperature, max_tokens)
    else:
        raise RuntimeError("No LLM configured. Set ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL or OPENAI_API_KEY + OPENAI_BASE_URL.")


def stream_chat(
    messages: list[dict[str, object]],
    *,
    tools: list[dict[str, object]] | None = None,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    on_delta: Callable[[str], None] | None = None,
    session_id: str | None = None,
    reasoning_effort: str = "default",
) -> dict[str, object]:
    """Stream a text turn from an OpenAI-compatible provider.

    The gateway deliberately exposes a small provider-neutral contract. The
    caller receives incremental text through ``on_delta`` and a final result
    containing the accumulated content, usage and finish reason.
    """
    if _detect_api_provider() != "openai":
        raise RuntimeError("Streaming currently requires an OpenAI-compatible provider.")
    base = chat_completions_base_url(env("OPENAI_BASE_URL"))
    payload: dict[str, object] = {
        "model": model or env("OPENAI_MODEL", "deepseek-chat"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if reasoning_effort in {"low", "medium", "high"}:
        payload["reasoning_effort"] = reasoning_effort
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            **openai_request_headers(env("OPENAI_API_KEY"), session_id=session_id, base_url=base),
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    content: list[str] = []
    usage: dict[str, object] = {}
    finish_reason = ""
    tool_calls: dict[int, dict[str, object]] = {}
    with urllib.request.urlopen(req, timeout=_llm_timeout(), context=ssl_context()) as response:
        for event in _iter_sse_json(response):
            choices = event.get("choices", [])
            if choices:
                choice = choices[0] if isinstance(choices[0], dict) else {}
                delta = choice.get("delta", {})
                text = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(text, str) and text:
                    content.append(text)
                    if on_delta:
                        on_delta(text)
                reason = choice.get("finish_reason")
                if reason:
                    finish_reason = str(reason)
                raw_calls = choice.get("delta", {}).get("tool_calls", []) if isinstance(choice.get("delta", {}), dict) else []
                for raw_call in raw_calls if isinstance(raw_calls, list) else []:
                    if not isinstance(raw_call, dict):
                        continue
                    index = int(raw_call.get("index", 0))
                    call = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if raw_call.get("id"):
                        call["id"] = str(raw_call["id"])
                    function = raw_call.get("function", {})
                    if isinstance(function, dict):
                        if function.get("name"):
                            call["name"] = str(function["name"])
                        if function.get("arguments"):
                            call["arguments"] = str(call["arguments"]) + str(function["arguments"])
            if isinstance(event.get("usage"), dict):
                usage = dict(event["usage"])
    parsed_calls: list[dict[str, object]] = []
    for call in tool_calls.values():
        try:
            arguments = json.loads(str(call["arguments"])) if call["arguments"] else {}
        except json.JSONDecodeError:
            arguments = {}
        parsed_calls.append({"id": call["id"], "name": call["name"], "arguments": arguments})
    return {
        "content": "".join(content),
        "usage": usage,
        "finish_reason": finish_reason or "stop",
        "tool_calls": parsed_calls,
    }


def _iter_sse_json(response: object) -> Iterator[dict[str, object]]:
    """Yield JSON payloads from an SSE response, ignoring comments/keepalives."""
    for raw in response:  # type: ignore[union-attr]
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _chat_openai(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    model: str | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, object]:
    """OpenAI-compatible API call with function calling."""
    base_url = env("OPENAI_BASE_URL")
    api_key = env("OPENAI_API_KEY")
    resolved_model = model or env("OPENAI_MODEL", "deepseek-chat")
    base = chat_completions_base_url(base_url)
    payload: dict[str, object] = {
        "model": resolved_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=openai_request_headers(api_key, base_url=base_url),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_llm_timeout(), context=ssl_context()) as response:
        data = json.loads(response.read().decode("utf-8"))

    choice = data["choices"][0]
    message = choice["message"]
    finish_reason = choice.get("finish_reason", "stop")

    result: dict[str, object] = {"content": message.get("content"), "tool_calls": [], "finish_reason": finish_reason}
    raw_tool_calls = message.get("tool_calls", [])
    if raw_tool_calls:
        result["tool_calls"] = [
            {
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"],
            }
            for tc in raw_tool_calls
        ]
    return result


def _chat_anthropic(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]],
    model: str | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, object]:
    """Anthropic API call with tool use."""
    base_url = env("ANTHROPIC_BASE_URL").rstrip("/")
    api_key = env("ANTHROPIC_API_KEY")
    resolved_model = model or env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    # Extract system message
    system_text = ""
    user_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            system_text = str(msg.get("content", ""))
        else:
            user_messages.append(msg)

    # Convert tool results to Anthropic format
    anthropic_messages: list[dict[str, object]] = []
    for msg in user_messages:
        role = msg.get("role", "user")
        if role == "tool":
            # Tool results are appended as user messages with tool_result blocks
            if anthropic_messages and anthropic_messages[-1].get("role") == "user":
                anthropic_messages[-1]["content"] = anthropic_messages[-1].get("content", [])  # type: ignore
                if isinstance(anthropic_messages[-1]["content"], str):
                    anthropic_messages[-1]["content"] = [{"type": "text", "text": anthropic_messages[-1]["content"]}]  # type: ignore
                anthropic_messages[-1]["content"].append({  # type: ignore
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": str(msg.get("content", "")),
                })
            continue
        content = str(msg.get("content", ""))
        if isinstance(msg.get("content"), list):
            content = msg["content"]  # type: ignore
        # Handle assistant messages with tool_calls
        if role == "assistant" and msg.get("tool_calls"):
            content_blocks: list[dict[str, object]] = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": str(msg["content"])})
            for tc in msg["tool_calls"]:  # type: ignore
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],  # type: ignore
                    "name": tc["function"]["name"],  # type: ignore
                    "input": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"],  # type: ignore
                })
            anthropic_messages.append({"role": "assistant", "content": content_blocks})
        else:
            anthropic_messages.append({"role": role, "content": content})

    # Convert tools to Anthropic format
    anthropic_tools = []
    for tool in tools:
        func = tool.get("function", {})
        anthropic_tools.append({
            "name": func.get("name", ""),
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {}),
        })

    payload: dict[str, object] = {
        "model": resolved_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": anthropic_messages,
    }
    if system_text:
        payload["system"] = system_text
    if anthropic_tools:
        payload["tools"] = anthropic_tools

    req = urllib.request.Request(
        f"{base_url}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_llm_timeout(), context=ssl_context()) as response:
        data = json.loads(response.read().decode("utf-8"))

    content_blocks = data.get("content", [])
    stop_reason = data.get("stop_reason", "end_turn")

    # Extract text and tool_use from response
    text_content = ""
    tool_calls = []
    for block in content_blocks:
        if block.get("type") == "text":
            text_content += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "name": block["name"],
                "arguments": block["input"],
            })

    return {
        "content": text_content or None,
        "tool_calls": tool_calls,
        "finish_reason": stop_reason,
    }


def agentic_loop(
    user_message: str,
    tools: list[dict[str, object]],
    system_prompt: str,
    execute_fn: Any,
    history: list[dict[str, object]] | None = None,
    max_rounds: int = 10,
) -> tuple[list[dict[str, object]], str]:
    """Run an agentic loop: user → LLM → tool calls → LLM → ... until final reply.

    Args:
        user_message: The user's input text.
        tools: Tool definitions for function calling.
        system_prompt: System prompt for the LLM.
        execute_fn: Callable(name: str, arguments: dict) -> str that runs a tool.
        history: Previous conversation messages (optional).
        max_rounds: Maximum tool-call rounds to prevent infinite loops.

    Returns:
        (messages, final_reply) — full message history and the final text reply.
    """
    messages: list[dict[str, object]] = list(history or [])
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    final_reply = ""

    for _ in range(max_rounds):
        response = chat_with_tools(messages, tools)
        assistant_msg: dict[str, object] = {"role": "assistant", "content": response.get("content") or ""}

        tool_calls = response.get("tool_calls", [])
        if tool_calls:
            assistant_tool_calls = []
            for tc in tool_calls:
                assistant_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)},
                })
            assistant_msg["tool_calls"] = assistant_tool_calls
            messages.append(assistant_msg)

            for tc in tool_calls:
                result_str = execute_fn(tc["name"], tc["arguments"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })
        else:
            final_reply = response.get("content") or ""
            messages.append({"role": "assistant", "content": final_reply})
            break

    return messages, final_reply


def _sentence(context: str, keywords: list[str]) -> str:
    for sentence in context.replace("\n", " ").split(". "):
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords) and len(sentence) > 40:
            return sentence.strip() + "."
    return ""
