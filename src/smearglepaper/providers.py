"""Provider adapters and one cancellable, retry-aware streaming transport."""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from .cancellation import CancellationToken, Cancelled
from .collector import ssl_context

Notify = Callable[[str, dict[str, Any]], None]

@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool = True
    tools: bool = True
    reasoning_effort: bool = False


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, retry_after: float = 0) -> None:
        super().__init__(message)
        self.code, self.retryable, self.retry_after = code, retryable, retry_after


def classify_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, HTTPError):
        code = exc.code
        try:
            delay = min(60.0, max(0.0, float(exc.headers.get("Retry-After", "0"))))
        except (ValueError, TypeError, AttributeError):
            delay = 0.0
        exc.close()
        if code in {401, 403}:
            return ProviderError("authentication", "模型服务拒绝认证，请检查连接配置。")
        return ProviderError("rate_limit" if code == 429 else "timeout" if code == 408 else "server" if code >= 500 else "request",
                             f"模型服务返回 HTTP {code}。", retryable=code in {408, 429} or code >= 500, retry_after=delay)
    if isinstance(exc, TimeoutError):
        return ProviderError("timeout", "模型服务响应超时。", retryable=True)
    if isinstance(exc, URLError | OSError):
        return ProviderError("network", "模型连接中断。", retryable=True)
    return ProviderError("protocol", "模型响应格式无效。")


def iter_sse(response: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Parse SSE data frames, including multiline data and a final EOF frame."""
    parts: list[str] = []
    for raw in response:
        # Some test transports return complete frames rather than lines.
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line:
                if parts:
                    body = "\n".join(parts)
                    parts = []
                    if body == "[DONE]":
                        return
                    try:
                        item = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise ProviderError("protocol", "模型返回无效 SSE 数据。") from exc
                    if isinstance(item, dict):
                        yield item
            elif line.startswith("data:"):
                parts.append(line[5:].lstrip())
        if raw in {b"\n", b"\r\n"} and parts:
            body = "\n".join(parts)
            parts = []
            if body == "[DONE]":
                return
            item = json.loads(body)
            if isinstance(item, dict):
                yield item
    if parts and parts != ["[DONE]"]:
        item = json.loads("\n".join(parts))
        if isinstance(item, dict):
            yield item


class ProviderAdapter(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def request(self, messages: list[dict[str, Any]], options: dict[str, Any]) -> urllib.request.Request: ...
    def decode(self, event: dict[str, Any]) -> list[tuple[str, Any]]: ...


@dataclass
class OpenAIAdapter:
    base_url: str
    headers: dict[str, str]
    name: str = "openai"
    capabilities = ProviderCapabilities(reasoning_effort=True)

    def request(self, messages: list[dict[str, Any]], options: dict[str, Any]) -> urllib.request.Request:
        payload = {k: v for k, v in options.items() if k in {"model", "temperature", "max_tokens", "tools"} and v is not None}
        payload.update(messages=messages, stream=True, stream_options={"include_usage": True})
        if options.get("tools"):
            payload["tool_choice"] = "auto"
        if options.get("reasoning_effort") in {"low", "medium", "high"}:
            payload["reasoning_effort"] = options["reasoning_effort"]
        return urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions",
                                      data=json.dumps(payload).encode(), headers={**self.headers, "Accept": "text/event-stream"})

    def decode(self, event: dict[str, Any]) -> list[tuple[str, Any]]:
        if event.get("error"):
            raise ProviderError("server", "模型流返回错误。", retryable=True)
        result: list[tuple[str, Any]] = []
        for choice in event.get("choices", [])[:1]:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                result.append(("text", delta["content"]))
            if delta.get("reasoning_content") or delta.get("reasoning"):
                result.append(("reasoning", delta.get("reasoning_content") or delta["reasoning"]))
            for call in delta.get("tool_calls", []):
                result.append(("tool", {"index": call.get("index", 0), "id": call.get("id", ""),
                                        **call.get("function", {})}))
            if choice.get("finish_reason"):
                result.append(("finish", choice["finish_reason"]))
        if event.get("usage"):
            result.append(("usage", event["usage"]))
        return result


@dataclass
class AnthropicAdapter:
    base_url: str
    api_key: str
    name: str = "anthropic"
    capabilities = ProviderCapabilities()

    def request(self, messages: list[dict[str, Any]], options: dict[str, Any]) -> urllib.request.Request:
        converted: list[dict[str, Any]] = []
        system: list[str] = []
        for message in messages:
            role = message["role"]
            if role == "system":
                system.append(str(message.get("content", "")))
                continue
            content: Any = message.get("content") or ""
            if role == "tool":
                role = "user"
                content = [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content}]
            elif message.get("tool_calls"):
                content = ([{"type": "text", "text": content}] if content else []) + [
                    {"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                     "input": json.loads(call["function"]["arguments"])}
                    for call in message["tool_calls"]
                ]
            converted.append({"role": role, "content": content})
        payload = {k: options[k] for k in ("model", "temperature", "max_tokens")}
        payload.update(messages=converted, system="\n\n".join(system), stream=True)
        if options.get("tools"):
            payload["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                                 "input_schema": t["function"].get("parameters", {})} for t in options["tools"]]
        base = self.base_url.rstrip("/")
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        return urllib.request.Request(url, data=json.dumps(payload).encode(),
                                      headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                               "Content-Type": "application/json", "Accept": "text/event-stream"})

    def decode(self, event: dict[str, Any]) -> list[tuple[str, Any]]:
        kind = event.get("type")
        if kind == "error":
            error_type = event.get("error", {}).get("type", "")
            raise ProviderError("server", "模型流返回错误。", retryable=error_type in {"overloaded_error", "rate_limit_error"})
        if kind == "message_start":
            return [("usage", event.get("message", {}).get("usage", {}))]
        if kind == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                return [("tool", {"index": event["index"], "id": block["id"], "name": block["name"],
                                  "arguments": json.dumps(block["input"]) if block.get("input") else ""})]
        if kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return [("text", delta["text"])]
            if delta.get("type") == "thinking_delta":
                return [("reasoning", delta["thinking"])]
            if delta.get("type") == "input_json_delta":
                return [("tool", {"index": event["index"], "arguments": delta["partial_json"]})]
        if kind == "message_delta":
            result = [("usage", event.get("usage", {}))]
            if event.get("delta", {}).get("stop_reason"):
                result.append(("finish", event["delta"]["stop_reason"]))
            return result
        return []


def _transport(request: urllib.request.Request, token: CancellationToken, timeout: float) -> Iterator[tuple[str, Any]]:
    """Bounded reader queue keeps connect/read cancellable for the caller.

    The reader owns response cleanup. A blocked underlying socket is bounded by
    its timeout; no late callback can escape into a cancelled turn.
    """
    items: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=64)
    stop = threading.Event()
    responses: list[Any] = []

    def put(item: tuple[str, Any]) -> None:
        while not stop.is_set() and not token.is_set():
            try:
                items.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read() -> None:
        try:
            response = urllib.request.urlopen(request, timeout=timeout, context=ssl_context())
            responses.append(response)
            try:
                put(("connected", str(response.headers.get("x-request-id") or response.headers.get("request-id") or "")))
                if token.is_set() or stop.is_set():
                    return
                for item in iter_sse(response):
                    if token.is_set() or stop.is_set():
                        break
                    put(("data", item))
            finally:
                response.close()
        except Exception as exc:
            put(("error", exc))
        finally:
            put(("end", None))

    thread = threading.Thread(target=read, daemon=True, name="openmuse-provider-reader")
    thread.start()
    try:
        while True:
            token.check()
            try:
                kind, value = items.get(timeout=0.05)
            except queue.Empty:
                continue
            token.check()
            if kind == "end":
                return
            if kind == "error":
                raise value
            yield kind, value
    finally:
        stop.set()
        # urllib has no public abort API. Shutting down its socket unblocks a
        # pending read; the reader remains the sole owner of response.close().
        # Other response implementations retain the bounded timeout fallback.
        if token.is_set() and responses:
            raw = getattr(getattr(responses[0], "fp", None), "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        thread.join(timeout=0.05)


def stream(adapter: ProviderAdapter, messages: list[dict[str, Any]], options: dict[str, Any], *,
           token: CancellationToken, on_delta: Callable[[str], None] | None,
           notify: Notify, timeout: float = 60, retries: int = 2, retry_base: float = 0.5) -> dict[str, Any]:
    started = time.monotonic()
    content: list[str] = []
    usage: dict[str, Any] = {}
    calls: dict[int, dict[str, Any]] = {}
    request_id, finish = "", ""
    semantic_output = False
    first_token = False
    if options.get("reasoning_effort", "default") != "default" and not adapter.capabilities.reasoning_effort:
        notify("capability", {"unsupported": "reasoning_effort", "provider": adapter.name})
    for attempt in range(retries + 1):
        try:
            token.check()
            for kind, item in _transport(adapter.request(messages, options), token, timeout):
                if kind == "connected":
                    request_id = item
                    notify("connected", {"provider": adapter.name, "request_id": item})
                    continue
                notify("event", {"choices": len(item.get("choices", []))})
                for part, value in adapter.decode(item):
                    token.check()
                    if part in {"text", "reasoning", "tool"}:
                        semantic_output = True
                        if not first_token:
                            first_token = True
                            notify("first_token", {"latency_ms": round((time.monotonic() - started) * 1000, 1)})
                    if part == "text":
                        content.append(value)
                        if on_delta:
                            on_delta(value)
                    elif part == "reasoning":
                        notify("reasoning", {"text": value})
                    elif part == "usage":
                        usage.update(value)
                    elif part == "finish":
                        finish = value
                    elif part == "tool":
                        call = calls.setdefault(int(value["index"]), {"id": "", "name": "", "arguments": ""})
                        for key in ("id", "name"):
                            if value.get(key):
                                call[key] = value[key]
                        call["arguments"] += value.get("arguments", "")
            token.check()
            if not finish:
                raise ProviderError("incomplete_stream", "模型输出中断，已保留收到的内容。", retryable=True)
            if calls and finish in {"length", "max_tokens"}:
                raise ProviderError("tool_arguments", "模型达到输出上限，未执行可能不完整的工具调用。")
            parsed = []
            for call in calls.values():
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise ProviderError("tool_arguments", "工具参数不完整，未执行该工具。") from exc
                if not isinstance(arguments, dict) or not call["id"] or not call["name"]:
                    raise ProviderError("tool_arguments", "工具调用格式无效，未执行该工具。")
                parsed.append({**call, "arguments": arguments})
            return {"content": "".join(content), "usage": usage, "tool_calls": parsed,
                    "finish_reason": finish, "request_id": request_id, "cancelled": False}
        except Cancelled:
            return {"content": "".join(content), "usage": usage, "tool_calls": [],
                    "finish_reason": "cancelled", "request_id": request_id, "cancelled": True}
        except Exception as exc:
            token.check()
            error = classify_error(exc)
            if semantic_output or not error.retryable or attempt >= retries:
                notify("error", {"code": error.code, "retryable": error.retryable, "partial": semantic_output})
                raise error from exc
            delay = max(error.retry_after, retry_base * 2**attempt)
            notify("retrying", {"code": error.code, "attempt": attempt + 2, "delay_seconds": delay})
            if token.wait(delay):
                return {"content": "", "tool_calls": [], "usage": {}, "cancelled": True, "finish_reason": "cancelled"}
    raise AssertionError("unreachable")
