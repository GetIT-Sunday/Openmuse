from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"


def load_dotenv(path: Path | None = None) -> None:
    dotenv_path = path or ROOT_DIR / ".env"
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            # Explicit process environment values take precedence over .env.
            os.environ.setdefault(key, value)


load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def openai_connection_settings() -> dict[str, str | bool]:
    """Return user-editable OpenAI-compatible connection settings without exposing the key."""
    return {
        "base_url": env("OPENAI_BASE_URL"),
        "model": env("OPENAI_MODEL", "deepseek-chat"),
        "user_agent": env("LLM_USER_AGENT"),
        "api_key_configured": bool(env("OPENAI_API_KEY")),
    }


def save_openai_connection(
    base_url: str,
    model: str,
    api_key: str | None = None,
    user_agent: str | None = None,
    clear_api_key: bool = False,
) -> None:
    """Persist OpenAI-compatible settings to the project .env and update this process."""
    base_url = base_url.strip().rstrip("/")
    model = model.strip()
    if not base_url:
        raise ValueError("Base URL 不能为空。")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是完整的 http(s) 地址。")
    if not model:
        raise ValueError("模型名称不能为空。")
    values = {"OPENAI_BASE_URL": base_url, "OPENAI_MODEL": model}
    if clear_api_key:
        values["OPENAI_API_KEY"] = ""
    elif api_key is not None and api_key.strip():
        values["OPENAI_API_KEY"] = api_key.strip()
    if user_agent is not None and user_agent.strip():
        values["LLM_USER_AGENT"] = user_agent.strip()

    dotenv_path = ROOT_DIR / ".env"
    existing = dotenv_path.read_text(encoding="utf-8").splitlines() if dotenv_path.exists() else []
    output: list[str] = []
    written: set[str] = set()
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in values:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")
    payload = "\n".join(output).rstrip() + "\n"
    dotenv_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=dotenv_path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, dotenv_path)
        os.chmod(dotenv_path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    for key, value in values.items():
        os.environ[key] = value


def runtime_settings() -> dict[str, object]:
    return {
        "data_dir": str(DATA_DIR),
        "llm": {
            "provider": "anthropic" if env("ANTHROPIC_API_KEY") else "openai" if env("OPENAI_API_KEY") else "none",
            "anthropic": {
                "base_url": env("ANTHROPIC_BASE_URL"),
                "model": env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
                "api_key_configured": bool(env("ANTHROPIC_API_KEY")),
            },
            "openai": {
                "base_url": env("OPENAI_BASE_URL"),
                "model": env("OPENAI_MODEL", "deepseek-chat"),
                "api_key_configured": bool(env("OPENAI_API_KEY")),
            },
        },
        "wechat": {
            "app_id_configured": bool(env("WECHAT_APP_ID")),
            "app_secret_configured": bool(env("WECHAT_APP_SECRET")),
        },
        "feishu": {
            "webhook_configured": bool(env("FEISHU_WEBHOOK_URL")),
        },
        "github": {
            "token_configured": bool(env("GITHUB_TOKEN")),
        },
    }
