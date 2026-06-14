from __future__ import annotations

from pathlib import Path

from .config import ROOT_DIR
from .storage import read_json, write_json

MEMORY_DIR = ROOT_DIR / "memory"

SOURCE_PROFILES = {
    "PaperWeekly": {"priority": "S", "learn": ["论文主线", "实验解读", "贡献总结"], "avoid": ["过度学术化"]},
    "机器之心": {"priority": "S", "learn": ["技术报道结构", "图表嵌入", "领域背景"], "avoid": ["信息密度过高"]},
    "夕小瑶科技说": {"priority": "S", "learn": ["长文叙事", "读者痛点", "背景铺垫"], "avoid": ["篇幅过长导致主线发散"]},
    "AINLP": {"priority": "A", "learn": ["NLP学习者友好表达", "面试迁移", "技术学习路线"], "avoid": ["资源汇总感过强"]},
    "量子位": {"priority": "A", "learn": ["标题", "开头", "传播节奏"], "avoid": ["标题党", "热点化过强"]},
    "AI科技评论": {"priority": "A", "learn": ["学术生态", "专家视角", "行业连接"], "avoid": ["新闻报道感过强"]},
    "CV技术指南": {"priority": "A", "learn": ["CV论文快读", "模型结构图解释", "实验指标总结"], "avoid": ["快读导致深度不足"]},
}

STYLE_PATTERNS = {
    "rigorous": ["证据优先", "明确边界", "逐表核对", "区分主张与结果"],
    "popular": ["问题型标题", "读者痛点开头", "短段落", "避免标题党"],
    "interview": ["面试问题", "设计动机", "边界追问", "项目迁移"],
    "balanced": ["论文主线", "公众号可读性", "图表解读", "学习迁移"],
}

USER_STYLE_PROFILE = {
    "preferred_style": "研究生技术精读 + 公众号可读性",
    "target_audience": ["AI方向研究生", "算法岗候选人", "大模型学习者"],
    "avoid": ["标题党", "过度口语化", "技术不严谨", "只复述论文Abstract"],
    "must_have": ["问题意识", "论证链条", "图表解读", "边界意识", "面试迁移"],
}


class MemoryManager:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or MEMORY_DIR

    def ensure_defaults(self) -> dict[str, Path]:
        defaults: dict[str, object] = {
            "source_profiles.json": SOURCE_PROFILES,
            "style_patterns.json": STYLE_PATTERNS,
            "user_style_profile.json": USER_STYLE_PROFILE,
            "article_history.json": [],
        }
        paths = {}
        for name, payload in defaults.items():
            path = self.directory / name
            if not path.exists():
                write_json(path, payload)
            paths[name] = path
        return paths

    def source_profiles(self) -> dict[str, object]:
        self.ensure_defaults()
        return read_json(self.directory / "source_profiles.json", SOURCE_PROFILES)

    def style_patterns(self) -> dict[str, object]:
        self.ensure_defaults()
        return read_json(self.directory / "style_patterns.json", STYLE_PATTERNS)

    def user_style_profile(self) -> dict[str, object]:
        self.ensure_defaults()
        return read_json(self.directory / "user_style_profile.json", USER_STYLE_PROFILE)

    def append_article_history(self, record: dict[str, object]) -> Path:
        self.ensure_defaults()
        path = self.directory / "article_history.json"
        history = read_json(path, [])
        if not isinstance(history, list):
            history = []
        history.append(record)
        write_json(path, history[-200:])
        return path
