"""Portable Skill and Pack discovery for the AIGC Harness.

The registry deliberately keeps the portable contract filesystem based: a Skill
is a directory with ``SKILL.md`` and optional references/scripts/assets.  A Pack
is a distributable composition of Skills; it does not become a second runtime.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from builtins import list as builtin_list
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class SkillManifest:
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    entrypoint: str = "SKILL.md"
    permissions: dict[str, Any] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()

    @classmethod
    def from_file(cls, path: Path) -> SkillManifest:
        text = path.read_text(encoding="utf-8")
        match = _FRONT_MATTER.match(text)
        data: dict[str, Any] = {}
        if match:
            loaded = yaml.safe_load(match.group("body"))
            if isinstance(loaded, dict):
                data = loaded
        name = str(data.get("name") or path.parent.name)
        skill_id = str(data.get("id") or f"local.{path.parent.name}")
        requires = data.get("requires", ())
        if isinstance(requires, str):
            requires = (requires,)
        elif isinstance(requires, list):
            requires = tuple(str(item) for item in requires)
        else:
            requires = ()
        permissions = data.get("permissions", {})
        def _names(value: Any) -> tuple[str, ...]:
            if isinstance(value, str):
                return (value,)
            return tuple(str(item) for item in value) if isinstance(value, list) else ()
        return cls(
            id=skill_id,
            name=name,
            version=str(data.get("version", "0.1.0")),
            description=str(data.get("description", "")).strip(),
            entrypoint=str(data.get("entrypoint", "SKILL.md")),
            permissions=permissions if isinstance(permissions, dict) else {},
            requires=requires,
            inputs=_names(data.get("inputs", [])),
            outputs=_names(data.get("outputs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "permissions": self.permissions,
            "requires": list(self.requires),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
        }


@dataclass(frozen=True)
class SkillPackage:
    manifest: SkillManifest
    path: Path

    @property
    def entrypoint(self) -> Path:
        return self.path / self.manifest.entrypoint

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.path.is_dir():
            errors.append("skill directory does not exist")
        if not self.entrypoint.is_file():
            errors.append(f"missing entrypoint: {self.manifest.entrypoint}")
        if not self.manifest.id.strip():
            errors.append("manifest id is empty")
        return errors

    def instructions(self, max_chars: int = 4000) -> str:
        """Return portable instructions for a model context, bounded for safety."""
        try:
            return self.entrypoint.read_text(encoding="utf-8")[:max_chars]
        except OSError:
            return ""

    def capability_summary(self) -> str:
        description = self.manifest.description or "未提供描述"
        return f"- {self.manifest.name} ({self.manifest.id}): {description}"


@dataclass(frozen=True)
class PackManifest:
    id: str
    name: str
    version: str
    skills: tuple[str, ...]
    workflows: tuple[str, ...] = ()
    description: str = ""
    harness_requirement: str = "*"
    pack_requirements: tuple[PackRequirement, ...] = ()

    @classmethod
    def from_file(cls, path: Path) -> PackManifest:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Pack manifest must be a mapping: {path}")
        skills = loaded.get("skills", ())
        workflows = loaded.get("workflows", ())
        requires = loaded.get("requires", {})
        if not isinstance(requires, dict):
            requires = {}
        pack_requirements = requires.get("packs", ())
        return cls(
            id=canonical_pack_id(str(loaded.get("id", path.parent.name))),
            name=str(loaded.get("name", path.parent.name)),
            version=str(loaded.get("version", "0.1.0")),
            skills=_pack_paths(skills, "source"),
            workflows=_pack_paths(workflows, "source"),
            description=str(loaded.get("description", "")).strip(),
            harness_requirement=str(requires.get("harness", "*")),
            pack_requirements=_pack_requirements(pack_requirements),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "skills": list(self.skills),
            "workflows": list(self.workflows),
            "requires": {
                "harness": self.harness_requirement,
                "packs": [requirement.to_dict() for requirement in self.pack_requirements],
            },
        }


@dataclass(frozen=True)
class PackRequirement:
    id: str
    version: str = "*"

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version}


def canonical_pack_id(value: str) -> str:
    """Return the publisher/name form while accepting the original dotted ID."""
    value = value.strip()
    if "/" not in value and value.count(".") == 1:
        publisher, name = value.split(".", 1)
        return f"{publisher}/{name}"
    return value


def _pack_paths(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    paths: list[str] = []
    for item in value:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict) and item.get(key):
            paths.append(str(item[key]))
    return tuple(paths)


def _pack_requirements(value: Any) -> tuple[PackRequirement, ...]:
    if not isinstance(value, list):
        return ()
    requirements: list[PackRequirement] = []
    for item in value:
        if isinstance(item, str):
            pack_id, separator, version = item.partition("@")
            requirements.append(PackRequirement(canonical_pack_id(pack_id), version if separator else "*"))
        elif isinstance(item, dict) and item.get("id"):
            requirements.append(
                PackRequirement(canonical_pack_id(str(item["id"])), str(item.get("version", "*")))
            )
    return tuple(requirements)


class SkillRegistry:
    def __init__(self, root: Path | Iterable[Path]) -> None:
        self.roots = (root,) if isinstance(root, Path) else tuple(root)
        self.root = self.roots[0] if self.roots else Path("skills")

    def list(self) -> list[SkillPackage]:
        packages: list[SkillPackage] = []
        seen_ids: set[str] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for entrypoint in sorted(root.glob("*/SKILL.md")):
                package = SkillPackage(SkillManifest.from_file(entrypoint), entrypoint.parent)
                if package.manifest.id in seen_ids:
                    continue
                packages.append(package)
                seen_ids.add(package.manifest.id)
        return packages

    def get(self, skill_id_or_name: str) -> SkillPackage:
        for package in self.list():
            if skill_id_or_name in {package.manifest.id, package.manifest.name, package.path.name}:
                return package
        raise KeyError(f"Skill not found: {skill_id_or_name}")

    def validate(self) -> dict[str, builtin_list[str]]:
        return {package.manifest.id: package.validate() for package in self.list() if package.validate()}

    def export(self, skill_id_or_name: str, destination: Path) -> Path:
        package = self.get(skill_id_or_name)
        errors = package.validate()
        if errors:
            raise ValueError(f"Invalid skill {package.manifest.id}: {'; '.join(errors)}")
        target = destination / package.path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(package.path, target, dirs_exist_ok=True)
        return target


def discover_packs(root: Path) -> list[tuple[PackManifest, Path]]:
    if not root.is_dir():
        return []
    packs: list[tuple[PackManifest, Path]] = []
    for manifest_path in sorted(root.rglob("pack.yaml")):
        packs.append((PackManifest.from_file(manifest_path), manifest_path.parent))
    return packs


def export_pack(pack_root: Path, pack_id_or_name: str, skill_registry: SkillRegistry, destination: Path) -> list[Path]:
    for manifest, _path in discover_packs(pack_root):
        requested_id = canonical_pack_id(pack_id_or_name)
        if requested_id not in {manifest.id, manifest.name, _path.name}:
            continue
        return export_pack_package(manifest, _path, skill_registry, destination)
    raise KeyError(f"Pack not found: {pack_id_or_name}")


def export_pack_package(
    manifest: PackManifest,
    source: Path,
    skill_registry: SkillRegistry,
    destination: Path,
) -> list[Path]:
    """Export one known Pack as a self-contained directory."""
    target = destination / source.name
    if target.exists():
        raise FileExistsError(f"Pack export destination already exists: {target}")
    temporary = destination / f".{source.name}.{uuid.uuid4().hex}.tmp"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, temporary)
        exported: list[Path] = []
        for reference in manifest.skills:
            local = _local_pack_skill(source, reference)
            if local is None:
                exported.append(skill_registry.export(reference, temporary / "skills"))
                continue
            package = SkillPackage(SkillManifest.from_file(local / "SKILL.md"), local)
            errors = package.validate()
            if errors:
                raise ValueError(f"Invalid skill {package.manifest.id}: {'; '.join(errors)}")
            target_skill = temporary / "skills" / local.name
            if not target_skill.exists():
                shutil.copytree(local, target_skill)
            exported.append(target_skill)
        os.replace(temporary, target)
        return [target / path.relative_to(temporary) for path in exported]
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _local_pack_skill(source: Path, reference: str) -> Path | None:
    for candidate in (source / reference, source / "skills" / Path(reference).name):
        try:
            candidate.resolve().relative_to(source.resolve())
        except ValueError as exc:
            raise ValueError(f"Skill path escapes Pack root: {reference}") from exc
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None
