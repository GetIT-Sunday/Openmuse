"""Conservative, provider-independent prompt budgeting (not billed tokens)."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from .config import env


class ContextBudgetExceeded(ValueError):
    pass


def estimate_tokens(value: object) -> int:
    # UTF-8 bytes deliberately overestimate most text tokenizers. Include an
    # envelope reserve as well; this is not an exact provider tokenizer count.
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 16


def excerpt(text: str, byte_limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= byte_limit:
        return text
    return raw[:max(0, byte_limit - 32)].decode("utf-8", errors="ignore") + " …[已截断]"


@dataclass(frozen=True)
class ContextBudget:
    context_tokens: int = 32768
    output_tokens: int = 4096
    safety_tokens: int = 1024

    @classmethod
    def configured(cls) -> ContextBudget:
        result = cls(int(env("OPENMUSE_CONTEXT_TOKENS", "32768")),
                     int(env("OPENMUSE_OUTPUT_TOKENS", "4096")))
        if result.input_tokens < 512 or result.output_tokens < 1:
            raise ContextBudgetExceeded("上下文预算过小；请检查 OPENMUSE_CONTEXT_TOKENS 与 OPENMUSE_OUTPUT_TOKENS。")
        return result

    @property
    def input_tokens(self) -> int:
        return self.context_tokens - self.output_tokens - self.safety_tokens

    def prepare(self, messages: list[dict[str, Any]], tools: object = None) -> tuple[list[dict[str, Any]], dict[str, object]]:
        result = copy.deepcopy(messages)
        removed = 0
        compacted = 0
        def size() -> int:
            return estimate_tokens(result) + estimate_tokens(tools)

        # Tool results are durable elsewhere. Keep IDs and call/result pairing,
        # compact only text content, never arguments or the current user request.
        for limit in (4096, 2048, 512):
            if size() <= self.input_tokens:
                break
            for message in result:
                if message.get("role") == "tool" and estimate_tokens(message.get("content", "")) > limit:
                    message["content"] = json.dumps({"truncated": True,
                        "note": "完整结果已保存在本地；需要正文时请分页读取。",
                        "preview": excerpt(str(message.get("content", "")), limit)}, ensure_ascii=False)
                    compacted += 1
        # History contains only transcript messages. Never split this turn's
        # tool exchange, or remove the system/task context and latest request.
        latest_user = max((i for i, m in enumerate(result) if m.get("role") == "user"), default=0)
        summary_index = 1 if len(result) > 1 and str(result[1].get("content", "")).startswith("历史对话摘录") else 0
        oldest = 2 if summary_index else 1
        while size() > self.input_tokens and latest_user > oldest:
            if result[oldest].get("role") == "system":
                break
            result.pop(oldest)
            latest_user -= 1
            removed += 1
        if size() > self.input_tokens and summary_index:
            result[summary_index]["content"] = excerpt(str(result[summary_index]["content"]), 512)
        if size() > self.input_tokens:
            raise ContextBudgetExceeded("当前请求、任务材料或工具参数超过上下文预算；请缩短输入，或按模型实际窗口调整上下文配置。未发送模型请求。")
        return result, {"estimated_input_tokens": size(), "input_budget": self.input_tokens,
                        "reserved_output_tokens": self.output_tokens, "dropped_history_messages": removed,
                        "compacted_tool_results": compacted, "estimator": "conservative_utf8_bytes"}
