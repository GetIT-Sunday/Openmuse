from __future__ import annotations

import hashlib
import json
from pathlib import Path


def article_bundle_fingerprint(article_json: Path) -> dict[str, object]:
    if not article_json.exists():
        raise FileNotFoundError(f"Article artifact not found: {article_json}")
    payload = json.loads(article_json.read_text(encoding="utf-8"))
    paths = [article_json, article_json.with_suffix(".md"), article_json.with_suffix(".html")]
    if isinstance(payload, dict):
        for field in ("cover_path",):
            if payload.get(field):
                paths.append(Path(str(payload[field])))
        paths.extend(Path(str(item)) for item in payload.get("figure_paths", []) if item)
    unique = sorted({path.resolve() for path in paths if path.exists()}, key=str)
    digest = hashlib.sha256()
    files: list[dict[str, object]] = []
    for path in unique:
        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        digest.update(str(path).encode("utf-8"))
        digest.update(file_hash.encode("ascii"))
        files.append({"path": str(path), "sha256": file_hash, "size": len(content)})
    return {"sha256": digest.hexdigest(), "files": files}
