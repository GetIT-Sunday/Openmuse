from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path

from .agent_reviews import (
    TITLE_OVERCLAIM_WORDS,
    analyze_style,
    diagnose_article,
    publish_package,
    technical_review,
    wechat_review,
)
from .config import DATA_DIR
from .cancellation import Cancelled
from .cover import create_cover
from .evidence import format_evidence_context
from .figures import place_visuals
from .llm import ArticleWriter, _detect_api_provider, format_visual_evidence
from .memory import MemoryManager
from .models import Article, PaperMeta
from .renderer import WechatRenderer
from .storage import ensure_parent, write_json
from .wechat import WechatClient


class PaperWritingAgent:
    """Evidence-first paper writing agent with persisted stages and revision loop."""

    def __init__(
        self,
        writer: ArticleWriter | None = None,
        renderer: WechatRenderer | None = None,
        *,
        model_available: bool | None = None,
    ) -> None:
        self.writer = writer or ArticleWriter()
        self.renderer = renderer or WechatRenderer()
        self.model_available = _detect_api_provider() != "none" if model_available is None else model_available

    def run(
        self,
        paper: PaperMeta,
        parsed: dict[str, object],
        *,
        notes: str = "",
        original_article: str = "",
        revision_instruction: str = "",
        target_audience: str = "AI方向研究生和算法岗候选人",
        style_mode: str = "balanced",
        target_score: int = 85,
        max_revisions: int = 3,
        upload_images: bool = False,
    ) -> dict[str, object]:
        run_id = f"{paper.paper_id.replace('/', '_')}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        run_dir = DATA_DIR.parent / "workspace" / "paper_writing" / run_id
        dirs = {
            name: run_dir / name
            for name in ("inputs", "diagnosis", "intermediate", "drafts", "reviews", "outputs")
        }
        for directory in dirs.values():
            directory.mkdir(parents=True, exist_ok=True)
        stages: list[dict[str, object]] = []
        attempts: list[dict[str, object]] = []
        memory = MemoryManager(DATA_DIR.parent / "memory")
        memory_paths = memory.ensure_defaults()
        manifest_path = run_dir / "manifest.json"
        run_state: dict[str, object] = {
            "agent": "Paper Writing Agent",
            "run_id": run_id,
            "status": "running",
            "active_stage": "intake",
            "paper": paper.to_dict(),
            "target_score": target_score,
            "max_revisions": max_revisions,
            "target_audience": target_audience,
            "style_mode": style_mode,
            "revision_instruction": revision_instruction,
            "model_available": self.model_available,
            "upload_images": upload_images,
            "model_fallbacks": [],
            "stages": stages,
            "attempts": attempts,
            "final": None,
            "memory": {name: str(path) for name, path in memory_paths.items()},
        }

        def persist(active_stage: str | None = None) -> None:
            if active_stage is not None:
                run_state["active_stage"] = active_stage
            write_json(manifest_path, run_state)

        def local_draft() -> str:
            model = getattr(self.writer, "model", None)
            return ArticleWriter(model=model if isinstance(model, str) else None).write_local(paper, parsed)

        def call_model(
            active_stage: str,
            system: str,
            prompt: str,
            temperature: float,
            fallback: Callable[[], str],
        ) -> str:
            persist(active_stage)
            try:
                return self.writer._call_model(system, prompt, temperature=temperature)
            except Cancelled:
                raise
            except Exception as exc:
                self.model_available = False
                run_state["model_available"] = False
                model_fallbacks = run_state.get("model_fallbacks")
                if isinstance(model_fallbacks, list):
                    model_fallbacks.append(
                        {"stage": active_stage, "type": type(exc).__name__, "message": str(exc)}
                    )
                persist(active_stage)
                return fallback()

        persist()

        input_note = original_article or notes
        input_path = dirs["inputs"] / "input_note.md"
        input_path.write_text(input_note or "# No user note supplied\n", encoding="utf-8")
        intake = {
            "paper": paper.to_dict(),
            "target_audience": target_audience,
            "style_mode": style_mode,
            "target_score": target_score,
            "max_revisions": max_revisions,
            "input_note": str(input_path),
        }
        write_json(dirs["inputs"] / "intake.json", intake)
        stages.append(_stage("intake", [input_path, dirs["inputs"] / "intake.json"]))
        persist("prepare-evidence")

        self._ensure_parsed_artifact(paper, parsed)
        evidence_packet = build_evidence_packet(paper, parsed, notes)
        evidence_path = dirs["intermediate"] / "evidence_packet.md"
        evidence_path.write_text(evidence_packet, encoding="utf-8")
        stages.append(_stage("prepare-evidence", [evidence_path]))
        persist("note-diagnoser")

        diagnosis, diagnosis_md = diagnose_article(input_note, paper, target_audience)
        diagnosis_json = dirs["diagnosis"] / "article_diagnosis.json"
        diagnosis_path = dirs["diagnosis"] / "article_diagnosis.md"
        write_json(diagnosis_json, diagnosis)
        diagnosis_path.write_text(diagnosis_md, encoding="utf-8")
        stages.append(_stage("note-diagnoser", [diagnosis_path, diagnosis_json]))
        persist("style-analyst")

        style_profile, style_report = analyze_style(
            memory.source_profiles(),
            memory.style_patterns(),
            style_mode=style_mode,
            target_audience=target_audience,
        )
        style_json = dirs["intermediate"] / "style_profile.json"
        style_path = dirs["intermediate"] / "style_report.md"
        write_json(style_json, style_profile)
        style_path.write_text(style_report, encoding="utf-8")
        stages.append(_stage("style-analyst", [style_path, style_json]))
        persist("outline-planner")

        if original_article and revision_instruction:
            strategy = f"根据用户要求定向修订现有文章：{revision_instruction}"
        elif self.model_available:
            strategy = call_model(
                "outline-planner",
                "你是论文写作 Agent 的分析规划器。你只依据给定证据制定可执行写作策略。",
                strategy_prompt(paper, evidence_packet, diagnosis_md, style_report, target_audience, style_mode),
                0.2,
                lambda: local_strategy(paper, parsed),
            )
        else:
            strategy = local_strategy(paper, parsed)
        strategy = _strip_fence(strategy)
        strategy_path = dirs["intermediate"] / "outline.md"
        strategy_path.write_text(strategy, encoding="utf-8")
        stages.append(_stage("outline-planner", [strategy_path]))
        persist("draft-writer")

        if original_article and revision_instruction and self.model_available:
            markdown = call_model(
                "user-revision",
                "你是严谨的中文科技文章修订编辑。保留可靠内容，只执行用户明确提出的修改。",
                user_revision_prompt(paper, evidence_packet, original_article, revision_instruction, target_audience),
                0.25,
                lambda: original_article,
            )
        elif self.model_available:
            markdown = call_model(
                "draft-writer",
                "你是严谨的中文 AI 论文解读写作 Agent。输出可发布的 Markdown 正文。",
                draft_prompt(paper, evidence_packet, strategy, diagnosis_md, style_report, target_audience, style_mode),
                0.35,
                local_draft,
            )
        else:
            markdown = original_article if original_article and revision_instruction else local_draft()
        markdown = _strip_fence(markdown)

        best: tuple[int, Article, dict[str, object], dict[str, object], Path] | None = None
        current_markdown = markdown
        for revision in range(max_revisions + 1):
            current_markdown = _apply_guardrail_repairs(current_markdown, paper)
            current_markdown = _place_structured_visuals(current_markdown, parsed)
            article = self._article(paper, parsed, current_markdown)
            version = revision + 1
            revision_prefix = dirs["drafts"] / f"draft_v{version}"
            article_json = self._write_article_bundle(revision_prefix, article)
            technical, technical_md = technical_review(current_markdown, paper, parsed)
            wechat, wechat_md = wechat_review(current_markdown)
            technical_json = dirs["reviews"] / f"technical_review_v{version}.json"
            technical_path = dirs["reviews"] / f"technical_review_v{version}.md"
            wechat_json = dirs["reviews"] / f"wechat_review_v{version}.json"
            wechat_path = dirs["reviews"] / f"wechat_review_v{version}.md"
            write_json(technical_json, technical)
            technical_path.write_text(technical_md, encoding="utf-8")
            write_json(wechat_json, wechat)
            wechat_path.write_text(wechat_md, encoding="utf-8")
            score = min(int(technical["score"]), int(wechat["score"]))
            content_ready = _content_meets_target(technical, wechat, target_score)
            ready = _meets_target(technical, wechat, target_score)
            attempts.append(
                {
                    "revision": version,
                    "score": score,
                    "technical_score": technical["score"],
                    "wechat_score": wechat["score"],
                    "content_ready": content_ready,
                    "publish_ready": ready,
                    "ready": ready,
                    "article": str(article_json),
                    "technical_review": str(technical_json),
                    "wechat_review": str(wechat_json),
                }
            )
            stages.append(
                _stage(
                    f"review-draft-v{version}",
                    [article_json, technical_json, wechat_json],
                    score=score,
                    technical_score=technical["score"],
                    wechat_score=wechat["score"],
                    content_ready=content_ready,
                    publish_ready=ready,
                    ready=ready,
                )
            )
            persist(f"review-draft-v{version}")
            if best is None or _review_rank(technical, wechat, target_score) > _review_rank(best[2], best[3], target_score):
                best = (score, article, technical, wechat, article_json)
            if ready or revision >= max_revisions or not self.model_available or not _has_revisable_issues(technical, wechat):
                break

            current_markdown = _strip_fence(
                call_model(
                    f"revision-loop-v{version + 1}",
                    "你是论文写作 Agent 的修订编辑。只修复审稿指出的问题，不得引入无证据事实。",
                    revision_prompt(paper, evidence_packet, current_markdown, technical, wechat),
                    0.25,
                    lambda markdown=current_markdown: markdown,
                )
            )

        assert best is not None
        best_score, best_article, best_technical, best_wechat, _ = best
        asset_report: dict[str, object] = {
            "requested": upload_images,
            "uploaded": False,
            "replacements": {},
        }
        if upload_images and _content_meets_target(best_technical, best_wechat, target_score):
            persist("upload-images")
            try:
                prepared_markdown, replacements = WechatClient(dry_run=False).upload_markdown_images(best_article.markdown)
                if replacements:
                    best_article = self._article(paper, parsed, prepared_markdown)
                    best_technical, _ = technical_review(prepared_markdown, paper, parsed)
                    best_wechat, _ = wechat_review(prepared_markdown)
                    best_score = min(int(best_technical["score"]), int(best_wechat["score"]))
                asset_report = {
                    "requested": True,
                    "uploaded": bool(replacements),
                    "replacement_count": len(replacements),
                    "replacements": replacements,
                }
                asset_report_path = dirs["outputs"] / "asset_upload_report.json"
                write_json(asset_report_path, asset_report)
                stages.append(_stage("upload-images", [asset_report_path], **asset_report))
                persist("publish-packager")
            except Exception as exc:
                asset_report = {
                    "requested": True,
                    "uploaded": False,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
                asset_report_path = dirs["outputs"] / "asset_upload_report.json"
                write_json(asset_report_path, asset_report)
                stages.append(_stage_failure("upload-images", [asset_report_path], exc))
                persist("publish-packager")

        final_prefix = DATA_DIR / "articles" / f"{paper.paper_id.replace('/', '_')}.agent"
        final_json = self._write_article_bundle(final_prefix, best_article)
        final_review_path = DATA_DIR / "reviews" / f"{paper.paper_id.replace('/', '_')}.agent.json"
        combined_review = {"score": best_score, "technical_review": best_technical, "wechat_review": best_wechat}
        write_json(final_review_path, combined_review)
        content_ready = _content_meets_target(best_technical, best_wechat, target_score)
        ready = _meets_target(best_technical, best_wechat, target_score)

        final_md = dirs["outputs"] / "final_article.md"
        final_html = dirs["outputs"] / "final_article.html"
        final_json_workspace = dirs["outputs"] / "final_article.json"
        final_md.write_text(best_article.markdown, encoding="utf-8")
        final_html.write_text(best_article.html, encoding="utf-8")
        write_json(final_json_workspace, best_article.to_dict())
        write_json(dirs["reviews"] / "technical_review.json", best_technical)
        write_json(dirs["reviews"] / "wechat_review.json", best_wechat)
        (dirs["reviews"] / "technical_review.md").write_text(_review_copy(dirs["reviews"], "technical", best_technical), encoding="utf-8")
        (dirs["reviews"] / "wechat_review.md").write_text(_review_copy(dirs["reviews"], "wechat", best_wechat), encoding="utf-8")
        revision_report = _revision_report(attempts, target_score, ready)
        revision_report_path = dirs["reviews"] / "revision_report.md"
        revision_report_path.write_text(revision_report, encoding="utf-8")
        package_path = dirs["outputs"] / "publish_package.md"
        package_path.write_text(publish_package(best_article.markdown, paper, style_mode), encoding="utf-8")
        stages.append(
            _stage(
                "publish-packager",
                [final_json_workspace, final_md, final_html, package_path, revision_report_path],
                score=best_score,
                ready=ready,
            )
        )
        persist("complete")

        manifest = {
            "agent": "Paper Writing Agent",
            "run_id": run_id,
            "status": "success" if ready else "needs_human_review",
            "paper": paper.to_dict(),
            "target_score": target_score,
            "max_revisions": max_revisions,
            "target_audience": target_audience,
            "style_mode": style_mode,
            "model_available": self.model_available,
            "model_fallbacks": list(run_state.get("model_fallbacks", [])),
            "upload_images": upload_images,
            "stages": stages,
            "attempts": attempts,
            "final": {
                "article_json": str(final_json),
                "markdown": str(Path(f"{final_prefix}.md")),
                "html": str(Path(f"{final_prefix}.html")),
                "review": str(final_review_path),
                "technical_score": best_technical["score"],
                "wechat_score": best_wechat["score"],
                "content_ready": content_ready,
                "publish_ready": ready,
                "publish_package": str(package_path),
                "asset_upload": asset_report,
                "score": best_score,
                "ready": ready,
                "model_fallback_used": bool(run_state.get("model_fallbacks")),
            },
            "memory": {name: str(path) for name, path in memory_paths.items()},
        }
        write_json(manifest_path, manifest)
        history_path = memory.append_article_history(
            {
                "run_id": run_id,
                "paper_id": paper.paper_id,
                "title": best_article.title,
                "style_mode": style_mode,
                "target_audience": target_audience,
                "score": best_score,
                "technical_score": best_technical["score"],
                "wechat_score": best_wechat["score"],
                "ready": ready,
                "manifest": str(manifest_path),
            }
        )
        manifest["memory"]["article_history.json"] = str(history_path)
        write_json(manifest_path, manifest)
        manifest["manifest"] = str(manifest_path)
        manifest["run_dir"] = str(run_dir)
        return manifest

    def _article(self, paper: PaperMeta, parsed: dict[str, object], markdown: str) -> Article:
        visuals = parsed.get("visuals", [])
        figure_paths = [
            str(item.get("path"))
            for item in visuals
            if isinstance(item, dict) and item.get("path")
        ]
        return Article(
            paper=paper,
            title=_title(markdown) or paper.title,
            digest=_digest(markdown),
            markdown=markdown,
            html=self.renderer.render(markdown),
            cover_path=create_cover(paper.title, "AI Paper Close Reading"),
            figure_paths=figure_paths,
            word_count=len(markdown),
        )

    @staticmethod
    def _write_article_bundle(prefix: Path, article: Article) -> Path:
        json_path = Path(f"{prefix}.json")
        markdown_path = Path(f"{prefix}.md")
        html_path = Path(f"{prefix}.html")
        ensure_parent(json_path)
        write_json(json_path, article.to_dict())
        markdown_path.write_text(article.markdown, encoding="utf-8")
        html_path.write_text(article.html, encoding="utf-8")
        return json_path

    @staticmethod
    def _ensure_parsed_artifact(paper: PaperMeta, parsed: dict[str, object]) -> None:
        parsed_path = DATA_DIR / "parsed" / f"{paper.paper_id.replace('/', '_')}.json"
        if not parsed_path.exists():
            write_json(parsed_path, parsed)


def build_evidence_packet(paper: PaperMeta, parsed: dict[str, object], notes: str = "") -> str:
    sections_and_claims = format_evidence_context(parsed, max_entries=48)
    visuals = format_visual_evidence(parsed)
    text = str(parsed.get("text", "")).strip()[:30_000]
    return f"""# Paper Writing Agent Evidence Packet

## 论文信息

- 标题：{paper.title}
- 作者：{", ".join(paper.authors) or "未提供"}
- 分类：{", ".join(paper.categories) or "未提供"}
- 链接：{paper.url}
- 摘要：{paper.abstract}

## 用户笔记

{notes or "未提供额外笔记。"}

## 章节、主张与证据

{sections_and_claims or "未提取到结构化证据。"}

## 图表索引

{visuals or "未提取到图表。"}

## 原文证据

{text}
"""


def strategy_prompt(
    paper: PaperMeta,
    evidence_packet: str,
    diagnosis: str,
    style_report: str,
    target_audience: str,
    style_mode: str,
) -> str:
    return f"""请为《{paper.title}》制定公众号论文解读写作策略。

输出必须包含：
1. 一句中心论点：严格限定在原论文证据范围内；
2. 目标读者与读者入口；
3. 6-10 个问题型小标题组成的论证链；
4. 每个核心 Figure/Table 应回答的问题与放置位置；
5. 至少 5 条“这篇论文没有证明什么”；
6. 原论文结论、编辑解释、后续历史影响的边界；
7. 3 个克制、可由正文兑现的标题候选；
8. 推荐开头策略；
9. 面试或项目迁移问题。

不要写正文，不要套用其他论文的固定章节结构。

目标读者：{target_audience}
风格模式：{style_mode}

原稿诊断：
{diagnosis}

风格报告：
{style_report}

证据包：
{evidence_packet}
"""


def draft_prompt(
    paper: PaperMeta,
    evidence_packet: str,
    strategy: str,
    diagnosis: str,
    style_report: str,
    target_audience: str,
    style_mode: str,
) -> str:
    return f"""请依据证据包和写作策略，为《{paper.title}》生成中文 Markdown 论文解读文章。

硬性要求：
1. 这是一篇论文讲解，不是主题知识教程；
2. 沿“问题 -> 假设 -> 设计 -> 证据 -> 边界”推进；
3. 开头 300 字内提出核心问题和阅读价值；
4. 每个方法组件解释它解决的问题、工作方式、作用和边界；
5. 每个实验使用“待验证主张 -> 设置 -> 观察 -> 能推出什么 -> 不能推出什么”；
6. 对关键 Figure/Table 按原论文编号解释，不编造图表；
7. 实验数字必须来自证据包，并说明任务、指标或比较范围；
8. 明确包含“这篇论文没有证明什么”和“面试 / 学习迁移”；
9. 后续影响只能放在文章后段，并标明不是原论文实验结论；
10. 保留论文链接；
11. 输出正文，不解释写作过程。

目标读者：{target_audience}
风格模式：{style_mode}

原稿诊断：
{diagnosis}

风格报告：
{style_report}

写作策略：
{strategy}

证据包：
{evidence_packet}
"""


def user_revision_prompt(
    paper: PaperMeta,
    evidence_packet: str,
    markdown: str,
    instruction: str,
    target_audience: str,
) -> str:
    return f"""请根据用户要求修订《{paper.title}》的现有文章。

用户要求：
{instruction}

目标读者：{target_audience}

约束：
1. 只执行用户要求涉及的修改，保留其他可靠内容与整体结构；
2. 所有事实、实验数字和结论必须受证据包支持；
3. 不得删除论文链接，不得引入无证据事实；
4. 输出完整 Markdown 正文，不解释修改过程。

现有文章：
{markdown}

证据包：
{evidence_packet}
"""


def revision_prompt(
    paper: PaperMeta,
    evidence_packet: str,
    markdown: str,
    technical: dict[str, object],
    wechat: dict[str, object],
) -> str:
    return f"""请修订《{paper.title}》的文章草稿，使其通过审稿。

修订规则：
- 优先修复高严重度问题、无法定位的数字和不存在的图表引用；
- 保留已经可追溯的结论；
- 不得增加证据包之外的实验事实；
- 若证据不足，收敛表述或明确写出边界；
- 保持 Markdown，直接输出修订后的完整正文。

技术审稿报告：
{technical}

公众号审稿报告：
{wechat}

当前草稿：
{markdown}

证据包：
{evidence_packet}
"""


def local_strategy(paper: PaperMeta, parsed: dict[str, object]) -> str:
    sections = parsed.get("sections", [])
    section_titles = [
        str(item.get("title", ""))
        for item in sections
        if isinstance(item, dict) and item.get("title")
    ][:8]
    return f"""# 写作策略

- 中心论点：解释《{paper.title}》提出的问题、设计、证据和边界。
- 论证顺序：研究问题 -> 方法主线 -> 理论或实验验证 -> 没有证明什么 -> 学习迁移。
- 原论文章节线索：{" -> ".join(section_titles) or "未提取到章节"}。
- 约束：不编造数字，不把后续影响写成原论文结论。
"""


def _place_structured_visuals(markdown: str, parsed: dict[str, object]) -> str:
    visuals = parsed.get("visuals", [])
    if isinstance(visuals, list) and visuals:
        return place_visuals(markdown, visuals)
    return markdown


def _apply_guardrail_repairs(markdown: str, paper: PaperMeta | None = None) -> str:
    repaired = markdown.rstrip()
    title = _title(repaired)
    if paper and (len(title) > 64 or any(word in title for word in TITLE_OVERCLAIM_WORDS)):
        safe_title = f"重读《{paper.title}》：它真正证明了什么？"
        repaired = re.sub(r"^#\s+.+$", f"# {safe_title}", repaired, count=1, flags=re.MULTILINE)
    if paper and not _has_short_question_opening(repaired):
        opening = (
            f"> 《{paper.title}》究竟解决了什么问题，又用哪些证据支撑结论？"
            "本文从设计、实验与边界三条线展开。"
        )
        repaired = re.sub(r"(^#\s+.+$\n)", rf"\1\n{opening}\n", repaired, count=1, flags=re.MULTILINE)
    boundary_terms = ("不是原论文", "并非原论文", "后续历史")
    if "后续影响" in repaired and not any(term in repaired for term in boundary_terms):
        disclaimer = "\n\n> 边界说明：以下后续影响属于论文发表后的历史发展，并非原论文自身的实验结论。\n"
        repaired = re.sub(r"(#{2,3}\s+[^\n]*后续影响[^\n]*\n)", rf"\1{disclaimer}", repaired, count=1)
        if disclaimer.strip() not in repaired:
            repaired += disclaimer
    if "这篇论文没有证明什么" not in repaired:
        repaired += (
            "\n\n## 这篇论文没有证明什么？\n\n"
            "- 当前证据只支持论文明确描述的任务、数据与指标，不能外推为所有场景下的通用能力。\n"
            "- 结果不能单独证明每个组件都不可替代，也不能替代独立复现与真实业务验证。\n"
        )
    if not any(term in repaired for term in ("面试", "学习迁移", "项目迁移")):
        repaired += (
            "\n\n## 面试与项目迁移：应该验证什么？\n\n"
            "- 面试解释：先说明论文要解决的问题，再区分设计、实验证据与结论边界。\n"
            "- 项目迁移：优先复现实验设置，并验证论文的关键假设在新数据和新任务上是否仍成立。\n"
        )
    return repaired + "\n"


def _has_short_question_opening(markdown: str) -> bool:
    for line in markdown.splitlines():
        text = re.sub(r"^>\s*", "", line.strip())
        if not text or text.startswith(("#", "!", "*", "-")):
            continue
        return len(text) <= 300 and any(mark in text for mark in ("？", "?", "为什么", "能否", "如何"))
    return False


def _meets_target(technical: dict[str, object], wechat: dict[str, object], target_score: int) -> bool:
    return _content_meets_target(technical, wechat, target_score) and not wechat.get("publication_blocking_issues")


def _content_meets_target(technical: dict[str, object], wechat: dict[str, object], target_score: int) -> bool:
    return (
        int(technical.get("score", 0)) >= target_score
        and int(wechat.get("score", 0)) >= target_score
        and not technical.get("blocking_issues")
        and not technical.get("revision_instructions")
        and not wechat.get("revision_instructions")
    )


def _has_revisable_issues(technical: dict[str, object], wechat: dict[str, object]) -> bool:
    return bool(technical.get("revision_instructions") or wechat.get("revision_instructions"))


def _review_rank(
    technical: dict[str, object],
    wechat: dict[str, object],
    target_score: int,
) -> tuple[int, int, int]:
    content_ready = _content_meets_target(technical, wechat, target_score)
    issue_count = sum(
        len(value)
        for report in (technical, wechat)
        for key, value in report.items()
        if key.endswith("issues") or key == "revision_instructions"
        if isinstance(value, list)
    )
    return int(content_ready), min(int(technical.get("score", 0)), int(wechat.get("score", 0))), -issue_count


def _stage(name: str, artifacts: list[Path], **detail: object) -> dict[str, object]:
    return {
        "name": name,
        "status": "success",
        "artifacts": [str(path) for path in artifacts],
        **detail,
    }


def _stage_failure(name: str, artifacts: list[Path], exc: Exception) -> dict[str, object]:
    return {
        "name": name,
        "status": "failed",
        "artifacts": [str(path) for path in artifacts],
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }


def _strip_fence(markdown: str) -> str:
    text = markdown.strip()
    match = re.match(r"^```(?:markdown|md)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _title(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _digest(markdown: str) -> str:
    for line in markdown.splitlines():
        text = line.strip()
        if text and not text.startswith(("#", "![", "*")):
            return re.sub(r"[*_>`]", "", text)[:120]
    return _title(markdown)[:120]


def _review_copy(review_dir: Path, kind: str, review: dict[str, object]) -> str:
    prefix = "Technical" if kind == "technical" else "WeChat"
    lines = [f"# {prefix} Review", "", f"- Score: {review.get('score', 0)}"]
    for key, value in review.items():
        if key == "score" or not isinstance(value, list):
            continue
        lines.extend(["", f"## {key}", *([f"- {item}" for item in value] or ["- None"])])
    return "\n".join(lines) + "\n"


def _revision_report(attempts: list[dict[str, object]], target_score: int, ready: bool) -> str:
    lines = ["# Revision Report", "", f"- Target score: {target_score}", f"- Ready: {ready}", "", "## Attempts"]
    for attempt in attempts:
        lines.append(
            f"- draft_v{attempt['revision']}: technical={attempt['technical_score']}, "
            f"wechat={attempt['wechat_score']}, gate={attempt['score']}, "
            f"content_ready={attempt.get('content_ready', False)}, publish_ready={attempt.get('publish_ready', False)}"
        )
    if not ready:
        lines.extend(["", "## Human Revision Required", "- 自动修订后仍未通过双门禁，请优先处理最终技术审稿和公众号审稿中的 revision_instructions。"])
    return "\n".join(lines) + "\n"
