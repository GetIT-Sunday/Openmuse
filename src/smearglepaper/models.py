from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class PaperMeta:
    paper_id: str
    title: str
    authors: list[str]
    abstract: str
    source: str
    url: str
    pdf_url: str | None
    published_at: str
    updated_at: str | None = None
    categories: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "PaperMeta":
        return cls(
            paper_id=str(data.get("paper_id", "")),
            title=str(data.get("title", "")),
            authors=[str(x) for x in data.get("authors", [])],
            abstract=str(data.get("abstract", "")),
            source=str(data.get("source", "")),
            url=str(data.get("url", "")),
            pdf_url=str(data["pdf_url"]) if data.get("pdf_url") else None,
            published_at=str(data.get("published_at", "")),
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
            categories=[str(x) for x in data.get("categories", [])],
            keywords=[str(x) for x in data.get("keywords", [])],
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Article:
    paper: PaperMeta
    title: str
    digest: str
    markdown: str
    html: str
    cover_path: str | None = None
    figure_paths: list[str] = field(default_factory=list)
    thumb_media_id: str | None = None
    word_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "paper": self.paper.to_dict(),
            "title": self.title,
            "digest": self.digest,
            "markdown": self.markdown,
            "html": self.html,
            "cover_path": self.cover_path,
            "figure_paths": self.figure_paths,
            "thumb_media_id": self.thumb_media_id,
            "word_count": self.word_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Article":
        return cls(
            paper=PaperMeta.from_dict(data["paper"]),  # type: ignore[arg-type]
            title=str(data.get("title", "")),
            digest=str(data.get("digest", "")),
            markdown=str(data.get("markdown", "")),
            html=str(data.get("html", "")),
            cover_path=str(data["cover_path"]) if data.get("cover_path") else None,
            figure_paths=[str(x) for x in data.get("figure_paths", [])],
            thumb_media_id=str(data["thumb_media_id"]) if data.get("thumb_media_id") else None,
            word_count=int(data.get("word_count", 0)),
        )


@dataclass
class BlogPost:
    title: str
    url: str
    source: str
    published_at: str
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class GitHubRepo:
    owner: str
    name: str
    full_name: str
    description: str
    stars: int
    forks: int
    language: str
    url: str
    collected_at: str
    weekly_star_growth: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "GitHubRepo":
        return cls(
            owner=str(data.get("owner", "")),
            name=str(data.get("name", "")),
            full_name=str(data.get("full_name", "")),
            description=str(data.get("description", "")),
            stars=int(data.get("stars", 0)),
            forks=int(data.get("forks", 0)),
            language=str(data.get("language", "") or ""),
            url=str(data.get("url", "")),
            collected_at=str(data.get("collected_at", "")),
            weekly_star_growth=int(data["weekly_star_growth"]) if data.get("weekly_star_growth") is not None else None,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
