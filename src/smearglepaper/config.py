from __future__ import annotations

import os
from pathlib import Path

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
            os.environ[key] = value


load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


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
