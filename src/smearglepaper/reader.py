from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Any

from .collector import ssl_context
from .config import DATA_DIR
from .evidence import build_paper_structure
from .models import PaperMeta
from .storage import ensure_parent, write_json

MAX_TEXT_CHARS = 120_000
CAPTION_RE = re.compile(r"^\s*((?:figure|fig\.?|table)\s+\d+[a-z]?\s*[:.\-]\s*.+)", re.IGNORECASE)


class PaperReader:
    def read(self, paper: PaperMeta) -> dict[str, object]:
        pdf_path = DATA_DIR / "pdfs" / f"{paper.paper_id.replace('/', '_')}.pdf"
        text = ""
        pages: list[dict[str, object]] = []
        visuals: list[dict[str, object]] = []
        if paper.pdf_url:
            ensure_parent(pdf_path)
            try:
                download_file(paper.pdf_url, pdf_path, timeout=45)
                text, pages = extract_pdf_text_pages(pdf_path)
                visuals = extract_pdf_visuals(pdf_path, paper.paper_id)
            except Exception as exc:  # noqa: BLE001 - reported in output for local diagnosis
                raise RuntimeError(f"PDF download/parse failed for {paper.paper_id}: {exc}") from exc

        sections, evidence_ledger = build_paper_structure(pages, visuals)
        figures = [str(item["path"]) for item in visuals]
        payload = {
            "paper": paper.to_dict(),
            "pdf_path": str(pdf_path) if pdf_path.exists() else None,
            "text": text[:MAX_TEXT_CHARS],
            "pages": pages,
            "figures": figures,
            "visuals": visuals,
            "sections": sections,
            "evidence_ledger": evidence_ledger,
            "figure_details": [item for item in visuals if item["kind"] == "figure"],
            "table_details": [item for item in visuals if item["kind"] == "table"],
        }
        out = DATA_DIR / "parsed" / f"{paper.paper_id.replace('/', '_')}.json"
        write_json(out, payload)
        return {
            "paper_id": paper.paper_id,
            "parsed": str(out),
            "text_chars": len(text),
            "pages": len(pages),
            "figures": figures,
            "visuals": visuals,
            "sections": len(sections),
            "evidence": len(evidence_ledger),
        }


def download_file(url: str, path: Path, timeout: int = 45) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "SmearglePaper/0.1"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as response:
        path.write_bytes(response.read())


def extract_pdf_text(path: Path) -> str:
    text, _ = extract_pdf_text_pages(path)
    return text


def extract_pdf_text_pages(path: Path) -> tuple[str, list[dict[str, object]]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", []

    reader = PdfReader(str(path))
    pages: list[dict[str, object]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        pages.append({"page": page_number, "text": page_text})
    return "\n\n".join(str(page["text"]) for page in pages).strip(), pages


def extract_pdf_figures(path: Path, paper_id: str, max_figures: int = 3) -> list[str]:
    """Backward-compatible path-only visual extraction API."""
    visuals = extract_pdf_visuals(path, paper_id, max_figures=max_figures, max_tables=0)
    return [str(item["path"]) for item in visuals]


def extract_pdf_visuals(
    path: Path,
    paper_id: str,
    *,
    max_figures: int = 4,
    max_tables: int = 4,
) -> list[dict[str, object]]:
    """Extract useful paper visuals with source metadata and nearby captions."""
    try:
        import fitz
    except ImportError:
        return []

    out_dir = DATA_DIR / "figures" / paper_id.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(path)
    figure_candidates: list[dict[str, Any]] = []
    table_candidates: list[dict[str, Any]] = []
    seen_xrefs: set[int] = set()

    try:
        for page_index, page in enumerate(doc):
            blocks = _text_blocks(page)
            for image in page.get_images(full=True):
                xref = int(image[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                extracted = doc.extract_image(xref)
                width = int(extracted.get("width", 0))
                height = int(extracted.get("height", 0))
                payload = extracted.get("image", b"")
                if width * height < 30_000 or not payload:
                    continue

                rects = page.get_image_rects(xref)
                bbox = max(rects, key=lambda rect: rect.get_area()) if rects else None
                caption = _nearest_caption(blocks, bbox, kind="figure")
                figure_candidates.append(
                    {
                        "kind": "figure",
                        "page": page_index + 1,
                        "caption": caption,
                        "source_bbox": _bbox_list(bbox),
                        "width": width,
                        "height": height,
                        "score": width * height + (2_000_000 if caption else 0),
                        "_payload": payload,
                        "_ext": str(extracted.get("ext", "png")),
                    }
                )

            if max_tables and hasattr(page, "find_tables"):
                for table in page.find_tables().tables:
                    bbox = fitz.Rect(table.bbox)
                    if bbox.get_area() < 10_000:
                        continue
                    caption = _nearest_caption(blocks, bbox, kind="table")
                    table_candidates.append(
                        {
                            "kind": "table",
                            "page": page_index + 1,
                            "caption": caption,
                            "source_bbox": _bbox_list(bbox),
                            "width": round(bbox.width, 1),
                            "height": round(bbox.height, 1),
                            "score": bbox.get_area() + (200_000 if caption else 0),
                            "_page": page,
                            "_bbox": bbox,
                        }
                    )
                table_candidates.extend(_caption_table_candidates(page, blocks, page_index + 1))

        selected = _select_candidates(figure_candidates, max_figures) + _select_candidates(table_candidates, max_tables)
        selected.sort(key=lambda item: (int(item["page"]), _visual_y(item)))

        visuals: list[dict[str, object]] = []
        kind_counts = {"figure": 0, "table": 0}
        for item in selected:
            kind = str(item["kind"])
            kind_counts[kind] += 1
            index = kind_counts[kind]
            if kind == "figure":
                visual_path = out_dir / f"figure_{index}.{item.pop('_ext')}"
                visual_path.write_bytes(item.pop("_payload"))
            else:
                page = item.pop("_page")
                bbox = item.pop("_bbox")
                clip = _padded_rect(bbox, page.rect, padding=12)
                visual_path = out_dir / f"table_{index}.png"
                page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).save(visual_path)

            item["id"] = f"{kind}-{index}"
            item["path"] = str(visual_path)
            item["score"] = round(float(item["score"]), 1)
            visuals.append(item)

        if not visuals and len(doc):
            visual_path = out_dir / "page_1_preview.png"
            page = doc[0]
            page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).save(visual_path)
            visuals.append(
                {
                    "id": "page-preview-1",
                    "kind": "page-preview",
                    "page": 1,
                    "caption": "",
                    "source_bbox": _bbox_list(page.rect),
                    "width": round(page.rect.width, 1),
                    "height": round(page.rect.height, 1),
                    "score": 0.0,
                    "path": str(visual_path),
                }
            )
        return visuals
    finally:
        doc.close()


def _select_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[object, ...]] = set()
    for item in sorted(candidates, key=lambda candidate: float(candidate["score"]), reverse=True):
        caption = str(item.get("caption", "")).lower()
        bbox = item.get("source_bbox", [])
        identity = caption if item["kind"] == "table" and caption else tuple(round(float(value) / 20) for value in bbox)
        key = (item["kind"], item["page"], identity)
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _visual_y(item: dict[str, Any]) -> float:
    bbox = item.get("source_bbox", [])
    return float(bbox[1]) if len(bbox) >= 2 else 0.0


def _text_blocks(page: Any) -> list[dict[str, object]]:
    blocks = []
    for block in page.get_text("blocks"):
        text = " ".join(str(block[4]).split())
        if text:
            blocks.append({"bbox": list(block[:4]), "text": text})
    return blocks


def _caption_table_candidates(page: Any, blocks: list[dict[str, object]], page_number: int) -> list[dict[str, Any]]:
    import fitz

    candidates: list[dict[str, Any]] = []
    ordered = sorted(blocks, key=lambda block: (float(block["bbox"][1]), float(block["bbox"][0])))
    for index, block in enumerate(ordered):
        match = CAPTION_RE.match(str(block["text"]))
        if not match or not match.group(1).lower().startswith("table"):
            continue

        crop = fitz.Rect(block["bbox"])
        previous_bottom = crop.y1
        for following in ordered[index + 1 :]:
            following_rect = fitz.Rect(following["bbox"])
            gap = following_rect.y0 - previous_bottom
            if gap > 28 or _looks_like_heading(str(following["text"])):
                break
            if following_rect.y0 >= crop.y0:
                crop.include_rect(following_rect)
                previous_bottom = max(previous_bottom, following_rect.y1)

        candidates.append(
            {
                "kind": "table",
                "page": page_number,
                "caption": match.group(1),
                "source_bbox": _bbox_list(crop),
                "width": round(crop.width, 1),
                "height": round(crop.height, 1),
                "score": crop.get_area() + 1_000_000,
                "_page": page,
                "_bbox": crop,
            }
        )
    return candidates


def _looks_like_heading(text: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+)*\s+[A-Z]", text.strip()))


def _nearest_caption(blocks: list[dict[str, object]], bbox: Any, *, kind: str) -> str:
    candidates: list[tuple[float, str]] = []
    for block in blocks:
        text = str(block["text"])
        match = CAPTION_RE.match(text)
        if not match or not match.group(1).lower().startswith(("table" if kind == "table" else ("figure", "fig"))):
            continue
        if bbox is None:
            candidates.append((0.0, match.group(1)))
            continue
        block_bbox = block["bbox"]
        vertical_distance = min(abs(float(block_bbox[1]) - bbox.y1), abs(bbox.y0 - float(block_bbox[3])))
        horizontal_distance = abs((float(block_bbox[0]) + float(block_bbox[2])) / 2 - (bbox.x0 + bbox.x1) / 2)
        candidates.append((vertical_distance + horizontal_distance * 0.15, match.group(1)))
    return min(candidates, default=(0.0, ""))[1]


def _bbox_list(bbox: Any) -> list[float]:
    if bbox is None:
        return []
    return [round(float(value), 1) for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1)]


def _padded_rect(bbox: Any, page_rect: Any, *, padding: float) -> Any:
    import fitz

    return fitz.Rect(
        max(page_rect.x0, bbox.x0 - padding),
        max(page_rect.y0, bbox.y0 - padding),
        min(page_rect.x1, bbox.x1 + padding),
        min(page_rect.y1, bbox.y1 + padding),
    )
