from __future__ import annotations

import sys
from pathlib import Path

from .blogs import BlogCollector
from .collector import ArxivCollector
from .config import DATA_DIR, runtime_settings
from .cover import create_cover
from .editor import improve_article_file
from .figures import place_figures, place_visuals
from .github_tracker import GitHubTracker
from .llm import ArticleWriter
from .models import Article, PaperMeta
from .notifier import FeishuNotifier
from .quality import review_article_file
from .ranker import rank_papers
from .reader import PaperReader
from .renderer import WechatRenderer
from .run_report import RunReport, generate_run_id
from .storage import read_json, write_dated_json, write_json
from .wechat import WechatClient


class SmearglePaperWorkflow:
    def __init__(self) -> None:
        self.collector = ArxivCollector()
        self.blog_collector = BlogCollector()
        self.github_tracker = GitHubTracker()
        self.notifier = FeishuNotifier()
        self.reader = PaperReader()
        self.writer = ArticleWriter()
        self.renderer = WechatRenderer()

    def collect(self, topic: str | None, query: str | None, days: int, sources: list[str], max_results: int) -> list[dict[str, object]]:
        if sources != ["arxiv"] and any(source != "arxiv" for source in sources):
            raise ValueError("Only arxiv is currently implemented.")
        papers = self.collector.collect(topic, query, days, max_results)
        payload = [paper.to_dict() for paper in papers]
        write_dated_json(DATA_DIR / "papers", "arxiv", payload)
        return payload

    def collect_blogs(self, topic: str | None, query: str | None, days: int, max_results: int, feeds: list[str] | None = None) -> dict[str, object]:
        result = self.blog_collector.collect(topic, query, days, max_results, feeds)
        write_dated_json(DATA_DIR / "blogs", "blogs", result)
        return result

    def agent_run(
        self,
        topic: str | None,
        query: str | None,
        paper_url: str | None,
        days: int,
        top_k: int,
        max_results: int,
        collect_blogs: bool,
        improve: bool,
        create_draft: bool,
        dry_run: bool,
        resume: bool = False,
    ) -> dict[str, object]:
        report = RunReport(
            run_id=generate_run_id(),
            workflow="agent-run",
            query=query or topic or "",
            resume=resume,
            dry_run=dry_run,
        )
        report.save()

        try:
            self._run_agent_pipeline(report, topic, query, paper_url, days, top_k, max_results,
                                     collect_blogs, improve, create_draft, dry_run, resume)
            failed_steps = [step for step in report.steps if step.status == "failed"]
            quality_passed = bool((report.article_quality or {}).get("pass", True))
            if failed_steps or not quality_passed:
                report.finish("partial_success", next_action="Review failed steps and article quality before publishing.")
            else:
                report.finish("success", next_action="Review the output files or publish.")
        except Exception as exc:
            report.fail_active_step(exc)
            report.finish("failed", next_action=f"Fix error: {exc}")
            report.save()
            raise

        report.save()
        result = report.to_dict()
        result["report_dir"] = str(report.run_dir)
        return result

    def _run_agent_pipeline(
        self,
        report: RunReport,
        topic: str | None,
        query: str | None,
        paper_url: str | None,
        days: int,
        top_k: int,
        max_results: int,
        collect_blogs: bool,
        improve: bool,
        create_draft: bool,
        dry_run: bool,
        resume: bool,
    ) -> None:
        # Step 1: Collect blogs
        blog_latest = DATA_DIR / "blogs" / "latest.json"
        if collect_blogs:
            step = report.start_step("collect-blogs")
            if resume and blog_latest.exists():
                read_json(blog_latest, {})
                report.complete_step(step, "skipped", "artifact_exists", [str(blog_latest)])
            else:
                self.collect_blogs(topic or "agents", query, days, max_results)
                report.complete_step(step, "success", artifacts=[str(blog_latest)])

        # Step 2: Collect and rank papers
        paper: PaperMeta
        if paper_url:
            step = report.start_step("resolve-paper-url")
            paper = self._resolve_paper(topic, query, paper_url, days, top_k)
            report.complete_step(step, "success", artifacts=[])
        else:
            papers_latest = DATA_DIR / "papers" / "latest.json"
            step = report.start_step("collect-arxiv")
            if resume and papers_latest.exists():
                papers = [PaperMeta.from_dict(item) for item in read_json(papers_latest, [])]
                report.complete_step(step, "skipped", "artifact_exists", [str(papers_latest)])
            else:
                papers = [PaperMeta.from_dict(item) for item in self.collect(topic, query, days, ["arxiv"], max_results)]
                report.complete_step(step, "success", artifacts=[str(papers_latest)])

            ranked_latest = DATA_DIR / "ranked" / "latest.json"
            step = report.start_step("rank-papers")
            if resume and ranked_latest.exists():
                ranked = read_json(ranked_latest, [])
                report.complete_step(step, "skipped", "artifact_exists", [str(ranked_latest)])
            else:
                ranked = self.rank(papers, top_k=top_k, query=query or topic)
                report.complete_step(step, "success", artifacts=[str(ranked_latest)])

            if not ranked:
                raise RuntimeError("No papers collected. Try a broader query or longer --days window.")
            paper = PaperMeta.from_dict(ranked[0]["paper"])  # type: ignore[arg-type]

        report.selected_paper = {
            "id": paper.paper_id,
            "title": paper.title,
            "url": paper.url,
        }

        # Step 3: Ingest paper
        pid = paper.paper_id.replace("/", "_")
        parsed_path = DATA_DIR / "parsed" / f"{pid}.json"
        step = report.start_step("ingest-paper")
        if resume and parsed_path.exists():
            parsed = {"parsed": str(parsed_path)}
            report.complete_step(step, "skipped", "artifact_exists", [str(parsed_path)])
        else:
            parsed = self.read(paper)
            report.complete_step(step, "success", artifacts=[str(parsed_path)])

        # Step 4: Generate article
        article_json_path = DATA_DIR / "articles" / f"{pid}.json"
        step = report.start_step("generate-article")
        article_changed = False
        if resume and article_json_path.exists():
            article_paths = {
                "paper_id": paper.paper_id,
                "article_json": str(article_json_path),
                "markdown": str(DATA_DIR / "articles" / f"{pid}.md"),
                "html": str(DATA_DIR / "articles" / f"{pid}.html"),
            }
            report.complete_step(step, "skipped", "artifact_exists", [str(article_json_path)])
        else:
            parsed_payload = read_json(Path(parsed["parsed"]), {})
            article_paths = self.write_article(paper, parsed=parsed_payload)
            article_changed = True
            report.complete_step(step, "success", artifacts=[str(article_json_path)])

        # Step 5: Review article
        article_json = Path(str(article_paths["article_json"]))
        review_path = DATA_DIR / "reviews" / f"{pid}.json"
        step = report.start_step("review-article")
        if resume and review_path.exists() and not article_changed:
            review = read_json(review_path, {})
            report.complete_step(step, "skipped", "artifact_exists", [str(review_path)])
        else:
            review = review_article_file(article_json)
            write_json(review_path, review)
            review_score = review.get("score", 0)
            review_threshold = review.get("threshold", 60)
            review_passed = review.get("pass", True)
            if review_passed:
                report.complete_step(step, "success", artifacts=[str(review_path)],
                                     quality={"score": review_score, "threshold": review_threshold, "pass": True})
            else:
                report.complete_step(step, "failed_quality_gate", "score_below_threshold", [str(review_path)],
                                     quality={"score": review_score, "threshold": review_threshold, "pass": False})

        review_passed = review.get("pass", True)
        report.article_quality = {
            "final_score": review.get("score", 0),
            "threshold": review.get("threshold", 60),
            "pass": review_passed,
        }

        # Step 6: Improve article (auto-trigger if review failed)
        final_article_json = article_json
        if not improve:
            step = report.start_step("improve-article")
            reason = "disabled" if review_passed else "review_failed_but_improve_disabled"
            report.complete_step(step, "skipped", reason)
        if improve:
            optimized_path = DATA_DIR / "articles" / f"{pid}.optimized.json"
            step = report.start_step("improve-article")
            triggered_by_review_failure = not review_passed
            if resume and optimized_path.exists():
                final_article_json = optimized_path
                optimized_review = review_article_file(optimized_path)
                review_passed = bool(optimized_review.get("pass", False))
                report.article_quality = {
                    "final_score": optimized_review.get("score", 0),
                    "threshold": optimized_review.get("threshold", 60),
                    "pass": review_passed,
                }
                if review_passed:
                    report.complete_step(step, "skipped", "artifact_exists", [str(optimized_path)])
                else:
                    report.complete_step(
                        step,
                        "failed_quality_gate",
                        "optimized_article_below_threshold",
                        [str(optimized_path)],
                        quality={
                            "score": optimized_review.get("score", 0),
                            "threshold": optimized_review.get("threshold", 60),
                            "pass": False,
                        },
                    )
            else:
                try:
                    improved = improve_article_file(article_json)
                    final_article_json = Path(str(improved["outputs"]["json"]))  # type: ignore[index]
                    optimized_review = improved.get("after", {})
                    review_passed = bool(optimized_review.get("pass", False))  # type: ignore[union-attr]
                    report.article_quality = {
                        "final_score": optimized_review.get("score", 0),  # type: ignore[union-attr]
                        "threshold": optimized_review.get("threshold", 60),  # type: ignore[union-attr]
                        "pass": review_passed,
                    }
                    if review_passed:
                        reason = "triggered_by_review_failure" if triggered_by_review_failure else None
                        report.complete_step(step, "success", reason, [str(final_article_json)])
                    else:
                        report.complete_step(
                            step,
                            "failed_quality_gate",
                            "optimized_article_below_threshold",
                            [str(final_article_json)],
                            quality={
                                "score": optimized_review.get("score", 0),  # type: ignore[union-attr]
                                "threshold": optimized_review.get("threshold", 60),  # type: ignore[union-attr]
                                "pass": False,
                            },
                        )
                except RuntimeError as exc:
                    report.complete_step(step, "failed", error={"type": "RuntimeError", "message": str(exc), "retryable": True})

        # Step 7: Create WeChat draft
        report.final_article_json = str(final_article_json)
        if create_draft and not review_passed:
            step = report.start_step("create-wechat-draft")
            report.complete_step(step, "skipped", "quality_gate_failed")
        elif create_draft:
            step = report.start_step("create-wechat-draft")
            try:
                article = Article.from_dict(read_json(final_article_json, {}))
                draft = WechatClient(dry_run=dry_run).create_draft(article)
                wechat_path = DATA_DIR / "wechat" / f"{pid}.json"
                write_json(wechat_path, draft)
                draft_id = draft.get("media_id", "") if isinstance(draft, dict) else ""
                report.wechat = {
                    "draft_created": True,
                    "draft_id": draft_id,
                    "published": False,
                    "dry_run": dry_run,
                }
                report.complete_step(step, "success", artifacts=[str(wechat_path)])
            except Exception as exc:
                report.complete_step(step, "failed", error={"type": type(exc).__name__, "message": str(exc), "retryable": True})
                report.wechat = {"draft_created": False, "published": False, "dry_run": dry_run}

    def load_papers_file(self, path: Path) -> list[PaperMeta]:
        return [PaperMeta.from_dict(item) for item in read_json(path, [])]

    def rank(self, papers: list[PaperMeta], top_k: int = 5, query: str | None = None) -> list[dict[str, object]]:
        rows = rank_papers(papers, query=query, top_k=top_k)
        write_dated_json(DATA_DIR / "ranked", "ranked", rows)
        return rows

    def read(self, paper: PaperMeta) -> dict[str, object]:
        return self.reader.read(paper)

    def write_article(self, paper: PaperMeta, parsed: dict[str, object] | None = None) -> dict[str, object]:
        markdown = self.writer.write(paper, parsed=parsed)
        figure_paths = [str(path) for path in (parsed or {}).get("figures", [])]
        visuals = (parsed or {}).get("visuals", [])
        if isinstance(visuals, list) and visuals:
            markdown = place_visuals(markdown, visuals)
        elif figure_paths:
            markdown = append_figures(markdown, figure_paths)
        html = self.renderer.render(markdown)
        digest = paper.abstract[:110] if paper.abstract else paper.title[:110]
        article = Article(
            paper=paper,
            title=_article_title(markdown, paper.title),
            digest=digest,
            markdown=markdown,
            html=html,
            cover_path=create_cover(paper.title, "AI Paper Close Reading"),
            figure_paths=figure_paths,
            word_count=len(markdown),
        )
        base = DATA_DIR / "articles" / paper.paper_id.replace("/", "_")
        article_json = Path(f"{base}.json")
        article_md = Path(f"{base}.md")
        article_html = Path(f"{base}.html")
        write_json(article_json, article.to_dict())
        article_md.write_text(markdown, encoding="utf-8")
        article_html.write_text(html, encoding="utf-8")
        return {"paper_id": paper.paper_id, "article_json": str(article_json), "markdown": str(article_md), "html": str(article_html), "cover": article.cover_path, "word_count": article.word_count}

    def run_writing_agent(
        self,
        paper_id: str | None = None,
        *,
        paper_url: str | None = None,
        paper_title: str | None = None,
        notes_path: Path | None = None,
        article_path: Path | None = None,
        revision_instruction: str = "",
        target_audience: str = "AI方向研究生和算法岗候选人",
        style_mode: str = "balanced",
        target_score: int = 85,
        max_revisions: int = 3,
        upload_images: bool = False,
        force_local: bool = False,
    ) -> dict[str, object]:
        from .writing_agent import PaperWritingAgent

        if not paper_id and paper_url:
            paper_id = paper_url.rstrip("/").split("/")[-1]
        if not paper_id:
            raise RuntimeError("Provide paper_id or paper_url.")
        pid = paper_id.replace("/", "_")
        parsed_path = DATA_DIR / "parsed" / f"{pid}.json"
        parsed = read_json(parsed_path, {})
        if not parsed and paper_url:
            resolved = self._resolve_paper(None, None, paper_url, 7, 1)
            parsed_result = self.read(resolved)
            parsed = read_json(Path(str(parsed_result["parsed"])), {})
        if not parsed:
            raise RuntimeError(f"Parsed paper not found: {parsed_path}. Run ingest-paper first.")
        paper_payload = parsed.get("paper", {})
        if not isinstance(paper_payload, dict):
            raise RuntimeError(f"Parsed paper metadata is invalid: {parsed_path}")
        paper = PaperMeta.from_dict(paper_payload)
        if paper_url:
            paper.url = paper_url
        if paper_title:
            paper.title = paper_title
        notes = notes_path.read_text(encoding="utf-8") if notes_path else ""
        original_article = article_path.read_text(encoding="utf-8") if article_path else ""
        return PaperWritingAgent(
            writer=self.writer,
            renderer=self.renderer,
            model_available=False if force_local else None,
        ).run(
            paper,
            parsed,
            notes=notes,
            original_article=original_article,
            revision_instruction=revision_instruction,
            target_audience=target_audience,
            style_mode=style_mode,
            target_score=target_score,
            max_revisions=max_revisions,
            upload_images=upload_images,
        )

    def review_article(self, path: Path) -> dict[str, object]:
        review = review_article_file(path)
        if path.suffix == ".json":
            article = Article.from_dict(read_json(path, {}))
            paper_id = article.paper.paper_id
        else:
            paper_id = path.stem
        review_path = DATA_DIR / "reviews" / f"{paper_id.replace('/', '_')}.json"
        write_json(review_path, review)
        review["output"] = str(review_path)
        return review

    def prepare_agent_assets(
        self,
        article_json: Path,
        *,
        target_score: int = 85,
    ) -> dict[str, object]:
        from .agent_reviews import technical_review, wechat_review
        from .writing_agent import _apply_guardrail_repairs, _content_meets_target, _meets_target

        article = Article.from_dict(read_json(article_json, {}))
        pid = article.paper.paper_id.replace("/", "_")
        parsed = read_json(DATA_DIR / "parsed" / f"{pid}.json", {})
        markdown = _apply_guardrail_repairs(article.markdown, article.paper)
        technical, _ = technical_review(markdown, article.paper, parsed)
        wechat, _ = wechat_review(markdown)
        if not _content_meets_target(technical, wechat, target_score):
            return {
                "status": "content_not_ready",
                "content_ready": False,
                "publish_ready": False,
                "technical_review": technical,
                "wechat_review": wechat,
            }

        prepared_markdown, replacements = WechatClient(dry_run=False).upload_markdown_images(markdown)
        prepared = Article(
            paper=article.paper,
            title=_article_title(prepared_markdown, article.title),
            digest=article.digest,
            markdown=prepared_markdown,
            html=self.renderer.render(prepared_markdown),
            cover_path=article.cover_path,
            figure_paths=article.figure_paths,
            thumb_media_id=article.thumb_media_id,
            word_count=len(prepared_markdown),
        )
        output_prefix = DATA_DIR / "articles" / f"{pid}.publish-ready"
        output_json = Path(f"{output_prefix}.json")
        output_md = Path(f"{output_prefix}.md")
        output_html = Path(f"{output_prefix}.html")
        write_json(output_json, prepared.to_dict())
        output_md.write_text(prepared.markdown, encoding="utf-8")
        output_html.write_text(prepared.html, encoding="utf-8")
        technical, _ = technical_review(prepared.markdown, prepared.paper, parsed)
        wechat, _ = wechat_review(prepared.markdown)
        publish_ready = _meets_target(technical, wechat, target_score)
        report = {
            "status": "publish_ready" if publish_ready else "needs_human_review",
            "content_ready": _content_meets_target(technical, wechat, target_score),
            "publish_ready": publish_ready,
            "replacement_count": len(replacements),
            "replacements": replacements,
            "technical_review": technical,
            "wechat_review": wechat,
            "article_json": str(output_json),
            "markdown": str(output_md),
            "html": str(output_html),
        }
        write_json(DATA_DIR / "reviews" / f"{pid}.publish-ready.json", report)
        return report

    def create_topic_draft(self, topic: str | None, query: str | None, paper_url: str | None, days: int, top_k: int, dry_run: bool) -> dict[str, object]:
        paper = self._resolve_paper(topic, query, paper_url, days, top_k)
        parsed = self.read(paper)
        parsed_payload = read_json(Path(parsed["parsed"]), {})
        article_paths = self.write_article(paper, parsed=parsed_payload)
        article = Article.from_dict(read_json(Path(article_paths["article_json"]), {}))
        report = WechatClient(dry_run=dry_run).create_draft(article)
        write_json(_wechat_result_path(paper.paper_id, real_wechat=not dry_run), report)
        return {"paper": paper.to_dict(), "parsed": parsed, "article": article_paths, "draft": report}

    def auto_publish(self, topic: str | None, query: str | None, paper_url: str | None, days: int, top_k: int, create_draft: bool, auto_publish: bool, no_publish: bool) -> dict[str, object]:
        dry_run = no_publish or not (create_draft or auto_publish)
        result = self.create_topic_draft(topic, query, paper_url, days, top_k, dry_run=dry_run)
        if auto_publish and not no_publish:
            media_id = result["draft"].get("media_id")  # type: ignore[union-attr]
            if media_id:
                result["publish"] = {"publish_id": WechatClient(dry_run=False).publish_draft(str(media_id))}
        return result

    def publish_existing_article(self, article_json: Path, real_wechat: bool, publish: bool) -> dict[str, object]:
        article = Article.from_dict(read_json(article_json, {}))
        client = WechatClient(dry_run=not real_wechat)
        report = client.create_draft(article)
        pid = article.paper.paper_id.replace("/", "_")
        write_json(_wechat_result_path(pid, real_wechat=real_wechat), report)
        if publish and real_wechat and report.get("media_id"):
            report["publish_id"] = client.publish_draft(str(report["media_id"]))
            write_json(DATA_DIR / "published" / f"{pid}.json", report)
        return report

    def update_existing_draft(self, article_json: Path, media_id: str, real_wechat: bool, index: int = 0) -> dict[str, object]:
        article = Article.from_dict(read_json(article_json, {}))
        report = WechatClient(dry_run=not real_wechat).update_draft(article, media_id, index)
        write_json(_wechat_result_path(article.paper.paper_id, real_wechat=real_wechat), report)
        return report

    def preflight(self) -> dict[str, object]:
        optional = {}
        for module in ("requests", "markdown", "pypdf", "fitz", "PIL", "mcp", "textual"):
            try:
                __import__(module)
                optional[module] = True
            except ImportError:
                optional[module] = False
        return {
            "settings": runtime_settings(),
            "python": {"version": sys.version.split()[0], "mcp_supported": sys.version_info >= (3, 10)},
            "optional_dependencies": optional,
        }

    def collect_github_stars(
        self,
        language: str | None = None,
        since: str = "weekly",
        max_results: int = 20,
    ) -> dict[str, object]:
        result = self.github_tracker.collect_with_growth(language, since, max_results)
        write_dated_json(DATA_DIR / "github", "github", result)
        return result

    def send_notification(self, title: str, content: str, msg_type: str = "interactive") -> dict[str, object]:
        return self.notifier.notify(title, content, msg_type)

    def daily_digest(
        self,
        topic: str | None = None,
        days: int = 7,
        top_k: int = 5,
        language: str | None = None,
        notify: bool = False,
        resume: bool = False,
    ) -> dict[str, object]:
        report = RunReport(
            run_id=generate_run_id(),
            workflow="daily-digest",
            query=topic or "",
            resume=resume,
            dry_run=False,
        )
        report.save()

        try:
            result: dict[str, object] = {"topic": topic, "days": days, "top_k": top_k}

            # Collect papers
            papers_latest = DATA_DIR / "papers" / "latest.json"
            step = report.start_step("collect-arxiv")
            if resume and papers_latest.exists():
                papers = read_json(papers_latest, [])
                report.complete_step(step, "skipped", "artifact_exists", [str(papers_latest)])
            else:
                papers = self.collect(topic, None, days, ["arxiv"], max(top_k * 5, 20))
                report.complete_step(step, "success", artifacts=[str(papers_latest)])

            # Rank papers
            ranked_latest = DATA_DIR / "ranked" / "latest.json"
            step = report.start_step("rank-papers")
            if resume and ranked_latest.exists():
                ranked = read_json(ranked_latest, [])
                report.complete_step(step, "skipped", "artifact_exists", [str(ranked_latest)])
            else:
                ranked = self.rank([PaperMeta.from_dict(p) for p in papers], top_k=top_k, query=topic)
                report.complete_step(step, "success", artifacts=[str(ranked_latest)])
            result["papers"] = {"count": len(ranked), "items": ranked}
            write_dated_json(DATA_DIR / "digest", "papers", ranked)

            # Collect blogs
            blogs_latest = DATA_DIR / "blogs" / "latest.json"
            step = report.start_step("collect-blogs")
            if resume and blogs_latest.exists():
                blogs = read_json(blogs_latest, {})
                report.complete_step(step, "skipped", "artifact_exists", [str(blogs_latest)])
            else:
                blogs = self.collect_blogs(topic or "agents", None, days, top_k * 3)
                report.complete_step(step, "success", artifacts=[str(blogs_latest)])
            result["blogs"] = {"count": blogs.get("count", 0), "items": blogs.get("posts", [])[:top_k]}
            write_dated_json(DATA_DIR / "digest", "blogs", blogs)

            # Collect GitHub stars
            github_latest = DATA_DIR / "github" / "latest.json"
            step = report.start_step("collect-github")
            if resume and github_latest.exists():
                github = read_json(github_latest, {})
                report.complete_step(step, "skipped", "artifact_exists", [str(github_latest)])
            else:
                github = self.collect_github_stars(language, "weekly", top_k * 2)
                report.complete_step(step, "success", artifacts=[str(github_latest)])
            result["github_stars"] = {"count": github.get("count", 0), "items": github.get("repos", [])[:top_k]}
            write_dated_json(DATA_DIR / "digest", "github", github)

            write_dated_json(DATA_DIR / "digest", "digest", result)

            # Notify
            if notify:
                step = report.start_step("notify-feishu")
                notifications: list[dict[str, object]] = []
                if ranked:
                    r = self.notifier.notify_papers(
                        f"📄 {topic or 'AI'} 论文日报 (Top {top_k})",
                        [row["paper"] if isinstance(row, dict) and "paper" in row else row for row in ranked],
                        top_k,
                    )
                    notifications.append({"type": "papers", "result": r})
                blog_posts = blogs.get("posts", [])
                if blog_posts:
                    r = self.notifier.notify_blogs(
                        f"📝 {topic or 'AI'} 博客日报 (Top {top_k})",
                        blog_posts,
                        top_k,
                    )
                    notifications.append({"type": "blogs", "result": r})
                repos = github.get("repos", [])
                if repos:
                    r = self.notifier.notify_github_stars(
                        f"⭐ GitHub Star 周报 (Top {top_k})",
                        repos,
                        top_k,
                    )
                    notifications.append({"type": "github_stars", "result": r})
                result["notifications"] = notifications
                report.complete_step(step, "success")

            report.finish("success", next_action="Review digest or deep-read a paper.")
            report.save()
            result["run_report"] = str(report.run_dir / "run_report.json")
            return result

        except Exception as exc:
            report.fail_active_step(exc)
            report.finish("failed", next_action=f"Fix error: {exc}")
            report.save()
            raise

    def trend_analysis(
        self,
        topic: str | None = None,
        days: int = 14,
        top_k: int = 10,
        language: str | None = None,
    ) -> dict[str, object]:
        import re
        from collections import Counter

        papers = self.collect(topic, None, days, ["arxiv"], max(top_k * 5, 50))
        blogs = self.collect_blogs(topic or "agents", None, days, top_k * 3)
        github = self.collect_github_stars(language, "weekly", top_k * 2)

        keyword_counter: Counter[str] = Counter()
        for paper in papers:
            text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
            words = re.findall(r"[a-z][a-z-]{3,}", text)
            keyword_counter.update(words)

        stop_words = {
            "the", "and", "for", "that", "this", "with", "from", "are", "was", "were",
            "been", "have", "has", "had", "not", "but", "can", "will", "our", "its",
            "which", "their", "than", "them", "these", "those", "into", "about", "such",
            "using", "used", "based", "propose", "proposed", "method", "methods",
            "approach", "model", "models", "results", "show", "shown", "paper", "work",
            "task", "tasks", "data", "set", "learning", "training", "performance",
        }
        hot_keywords = [
            (word, count) for word, count in keyword_counter.most_common(50)
            if word not in stop_words and len(word) > 3
        ][:20]

        blog_keywords: Counter[str] = Counter()
        for post in blogs.get("posts", []):
            title = post.get("title", "").lower()
            words = re.findall(r"[a-z][a-z-]{3,}", title)
            blog_keywords.update(words)
        blog_hot = [
            (word, count) for word, count in blog_keywords.most_common(20)
            if word not in stop_words and len(word) > 3
        ][:10]

        github_repos = github.get("repos", [])
        top_repos = sorted(github_repos, key=lambda r: r.get("weekly_star_growth") or 0, reverse=True)[:top_k]

        result: dict[str, object] = {
            "topic": topic,
            "days": days,
            "paper_count": len(papers),
            "blog_count": blogs.get("count", 0),
            "github_count": github.get("count", 0),
            "hot_keywords": [{"keyword": w, "count": c} for w, c in hot_keywords],
            "blog_hot_keywords": [{"keyword": w, "count": c} for w, c in blog_hot],
            "trending_repos": top_repos,
            "top_papers": [
                {"title": p.get("title", ""), "url": p.get("url", ""), "published_at": p.get("published_at", "")}
                for p in papers[:top_k]
            ],
        }
        write_dated_json(DATA_DIR / "digest", "trend", result)
        return result

    def check_artifacts(self, paper_id: str) -> dict[str, object]:
        """Check artifact completion status for a given paper_id."""
        pid = paper_id.replace("/", "_")
        artifacts: dict[str, dict[str, object]] = {}
        steps = [
            ("parsed", str(DATA_DIR / "parsed" / f"{pid}.json")),
            ("article_md", str(DATA_DIR / "articles" / f"{pid}.md")),
            ("article_json", str(DATA_DIR / "articles" / f"{pid}.json")),
            ("article_html", str(DATA_DIR / "articles" / f"{pid}.html")),
            ("review", str(DATA_DIR / "reviews" / f"{pid}.json")),
            ("optimized_md", str(DATA_DIR / "articles" / f"{pid}.optimized.md")),
            ("optimized_json", str(DATA_DIR / "articles" / f"{pid}.optimized.json")),
            ("wechat_draft", str(DATA_DIR / "wechat" / f"{pid}.json")),
            ("published", str(DATA_DIR / "published" / f"{pid}.json")),
        ]
        completed: list[str] = []
        remaining: list[str] = []
        for name, path in steps:
            exists = Path(path).exists()
            artifacts[name] = {"exists": exists, "path": path}
            if exists:
                completed.append(name)
            else:
                remaining.append(name)

        next_step = "done"
        for artifact_name, command in [
            ("parsed", "ingest-paper"),
            ("article_md", "generate-article"),
            ("article_json", "generate-article"),
            ("article_html", "generate-article"),
            ("review", "review-article"),
            ("optimized_md", "improve-article"),
            ("optimized_json", "improve-article"),
            ("wechat_draft", "create-wechat-draft"),
            ("published", "publish-draft"),
        ]:
            if artifact_name in remaining:
                next_step = command
                break
        return {
            "paper_id": paper_id,
            "artifacts": artifacts,
            "completed_steps": completed,
            "remaining_steps": remaining,
            "next_step": next_step,
        }

    def _resolve_paper(self, topic: str | None, query: str | None, paper_url: str | None, days: int, top_k: int) -> PaperMeta:
        if paper_url:
            paper_id = paper_url.rstrip("/").split("/")[-1]
            metadata = self.collector.fetch_by_id(paper_id)
            if metadata:
                return metadata
            return PaperMeta(paper_id=paper_id, title=f"Paper {paper_id}", authors=[], abstract="", source="url", url=paper_url, pdf_url=paper_url.replace("/abs/", "/pdf/"), published_at="")
        papers = [PaperMeta.from_dict(item) for item in self.collect(topic, query, days, ["arxiv"], max_results=max(top_k * 10, 20))]
        ranked = self.rank(papers, top_k=top_k, query=query or topic)
        if not ranked:
            raise RuntimeError("No papers collected. Try a broader query or longer --days window.")
        return PaperMeta.from_dict(ranked[0]["paper"])  # type: ignore[arg-type]


def _article_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _wechat_result_path(paper_id: str, *, real_wechat: bool) -> Path:
    directory = DATA_DIR / "wechat" if real_wechat else DATA_DIR / "wechat" / "dry_run"
    return directory / f"{paper_id.replace('/', '_')}.json"


def append_figures(markdown: str, figure_paths: list[str]) -> str:
    return place_figures(markdown, figure_paths)
