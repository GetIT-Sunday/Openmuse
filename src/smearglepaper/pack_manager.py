"""Dependency resolution and project-local installation for Capability Packs."""
from __future__ import annotations

import os
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from .skill_registry import (
    PackManifest,
    PackRequirement,
    SkillPackage,
    SkillRegistry,
    canonical_pack_id,
    discover_packs,
)
from .storage import read_json, write_json

_SEMVER = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")


class PackError(RuntimeError):
    """Raised when a Pack cannot be validated, resolved, or installed."""


def harness_version() -> str:
    try:
        return package_version("smearglepaper")
    except PackageNotFoundError:
        return "0.2.0"


def parse_version(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value.strip())
    if not match:
        raise PackError(f"Unsupported version '{value}'; expected MAJOR.MINOR.PATCH")
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))  # type: ignore[return-value]


def version_satisfies(version: str, constraint: str) -> bool:
    """Evaluate the intentionally small v1 constraint language: *, exact, >=, and ^."""
    constraint = constraint.strip() or "*"
    if constraint == "*":
        parse_version(version)
        return True
    actual = parse_version(version)
    if constraint.startswith(">="):
        return actual >= parse_version(constraint[2:].strip())
    if constraint.startswith("^"):
        minimum = parse_version(constraint[1:].strip())
        if minimum[0] > 0:
            maximum = (minimum[0] + 1, 0, 0)
        elif minimum[1] > 0:
            maximum = (0, minimum[1] + 1, 0)
        else:
            maximum = (0, 0, minimum[2] + 1)
        return minimum <= actual < maximum
    return actual == parse_version(constraint)


@dataclass(frozen=True)
class PackPackage:
    manifest: PackManifest
    path: Path

    def validate(self, current_harness_version: str | None = None, *, require_compatible: bool = True) -> list[str]:
        errors: list[str] = []
        if not _PACK_ID.fullmatch(self.manifest.id):
            errors.append("pack id must use publisher/name with lowercase letters, digits, '.', '_' or '-'")
        try:
            parse_version(self.manifest.version)
        except PackError as exc:
            errors.append(str(exc))
        try:
            compatible = version_satisfies(current_harness_version or harness_version(), self.manifest.harness_requirement)
        except PackError as exc:
            errors.append(f"invalid Harness requirement: {exc}")
        else:
            if require_compatible and not compatible:
                errors.append(
                    f"requires AIGC Harness {self.manifest.harness_requirement}, "
                    f"current version is {current_harness_version or harness_version()}"
                )
        if not (self.path / "pack.yaml").is_file():
            errors.append("missing pack.yaml")
        for workflow in self.manifest.workflows:
            if "/" not in workflow and not workflow.endswith((".yaml", ".yml")):
                continue
            candidate = self.path / workflow
            try:
                candidate.resolve().relative_to(self.path.resolve())
            except ValueError:
                errors.append(f"workflow path escapes Pack root: {workflow}")
                continue
            if not candidate.is_file():
                errors.append(f"missing workflow: {workflow}")
        return errors


class PackCatalog:
    """A deterministic catalog of Pack versions available from local directories."""

    def __init__(self, packages: Iterable[PackPackage] = ()) -> None:
        self._packages: dict[str, list[PackPackage]] = {}
        for package in packages:
            self.add(package)

    @classmethod
    def from_roots(cls, roots: Iterable[Path]) -> PackCatalog:
        catalog = cls()
        seen: set[Path] = set()
        for root in roots:
            resolved = root.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            for manifest, path in discover_packs(root):
                catalog.add(PackPackage(manifest, path))
        return catalog

    def add(self, package: PackPackage) -> None:
        packages = self._packages.setdefault(package.manifest.id, [])
        if any(item.manifest.version == package.manifest.version and item.path.resolve() == package.path.resolve() for item in packages):
            return
        packages.append(package)
        packages.sort(key=lambda item: parse_version(item.manifest.version), reverse=True)

    def candidates(self, requirement: PackRequirement) -> list[PackPackage]:
        return [
            package
            for package in self._packages.get(canonical_pack_id(requirement.id), [])
            if version_satisfies(package.manifest.version, requirement.version)
        ]

    def get(self, pack_id: str, constraint: str = "*") -> PackPackage:
        requirement = PackRequirement(canonical_pack_id(pack_id), constraint)
        candidates = self.candidates(requirement)
        if candidates:
            return candidates[0]
        available = ", ".join(package.manifest.version for package in self._packages.get(requirement.id, [])) or "none"
        raise PackError(f"Missing Pack {requirement.id}@{constraint}; available versions: {available}")


@dataclass(frozen=True)
class PackResolution:
    root: str
    packages: tuple[PackPackage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "install_order": [
                {
                    "id": package.manifest.id,
                    "version": package.manifest.version,
                    "dependencies": [item.to_dict() for item in package.manifest.pack_requirements],
                }
                for package in self.packages
            ],
        }


class PackResolver:
    def __init__(self, catalog: PackCatalog, current_harness_version: str | None = None) -> None:
        self.catalog = catalog
        self.harness_version = current_harness_version or harness_version()

    def resolve(self, pack_id: str, constraint: str = "*") -> PackResolution:
        root_id = canonical_pack_id(pack_id)
        selected = self._solve(PackRequirement(root_id, constraint), {})
        return PackResolution(root_id, self._installation_order(selected[root_id], selected))

    def resolve_package(self, root: PackPackage) -> PackResolution:
        requirement = PackRequirement(root.manifest.id, root.manifest.version)
        selected = self._solve(requirement, {root.manifest.id: root})
        return PackResolution(root.manifest.id, self._installation_order(root, selected))

    def _solve(self, root: PackRequirement, fixed: dict[str, PackPackage]) -> dict[str, PackPackage]:
        PendingRequirement = tuple[PackRequirement, tuple[str, ...]]

        def search(
            pending: list[PendingRequirement],
            selected: dict[str, PackPackage],
            constraints: dict[str, tuple[str, ...]],
        ) -> dict[str, PackPackage]:
            if not pending:
                return selected
            requirement, chain = pending[0]
            rest = pending[1:]
            pack_id = canonical_pack_id(requirement.id)
            if pack_id in chain:
                raise PackError(f"Circular Pack dependency: {' -> '.join((*chain, pack_id))}")
            pack_constraints = (*constraints.get(pack_id, ()), requirement.version)
            next_constraints = constraints | {pack_id: pack_constraints}
            existing = selected.get(pack_id)
            if existing:
                if all(version_satisfies(existing.manifest.version, item) for item in pack_constraints):
                    return search(rest, selected, next_constraints)
                raise PackError(
                    f"Conflicting requirements for {pack_id}: selected {existing.manifest.version}, "
                    f"constraints are {', '.join(pack_constraints)}"
                )

            available = [fixed[pack_id]] if pack_id in fixed else self.catalog.candidates(PackRequirement(pack_id, "*"))
            candidates = [
                package
                for package in available
                if all(version_satisfies(package.manifest.version, item) for item in pack_constraints)
            ]
            if not candidates:
                if not available:
                    raise PackError(f"Missing Pack {pack_id}@{requirement.version}; available versions: none")
                versions = ", ".join(package.manifest.version for package in available)
                raise PackError(f"Conflicting requirements for {pack_id}: {', '.join(pack_constraints)}; available: {versions}")

            failures: list[PackError] = []
            for candidate in candidates:
                errors = candidate.validate(self.harness_version)
                if errors:
                    failures.append(
                        PackError(f"Invalid Pack {pack_id}@{candidate.manifest.version}: {'; '.join(errors)}")
                    )
                    continue
                dependencies = [
                    (dependency, (*chain, pack_id)) for dependency in candidate.manifest.pack_requirements
                ]
                try:
                    return search(dependencies + rest, selected | {pack_id: candidate}, next_constraints)
                except PackError as exc:
                    failures.append(exc)
            raise failures[-1]

        return search([(root, ())], {}, {})

    @staticmethod
    def _installation_order(root: PackPackage, selected: dict[str, PackPackage]) -> tuple[PackPackage, ...]:
        order: list[PackPackage] = []
        visited: set[str] = set()

        def visit(package: PackPackage) -> None:
            if package.manifest.id in visited:
                return
            for dependency in package.manifest.pack_requirements:
                visit(selected[dependency.id])
            visited.add(package.manifest.id)
            order.append(package)

        visit(root)
        return tuple(order)


class InstalledPackStore:
    """Own project-local Pack files, lock state, and enablement state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.packs_dir = root / "packs"
        self.lock_path = root / "pack-lock.json"
        self.enabled_path = root / "enabled-packs.json"

    def installed_catalog(self) -> PackCatalog:
        return PackCatalog.from_roots((self.packs_dir,))

    def enabled_ids(self) -> set[str]:
        payload = read_json(self.enabled_path, {"packs": []})
        values = payload.get("packs", []) if isinstance(payload, dict) else []
        return {canonical_pack_id(str(value)) for value in values if isinstance(value, str)}

    def skill_roots(self) -> list[Path]:
        catalog = self.installed_catalog()
        roots: list[Path] = []
        for pack_id in sorted(self.enabled_ids()):
            try:
                root = catalog.get(pack_id).path / "skills"
            except PackError:
                continue
            if root.is_dir():
                roots.append(root)
        return roots

    def install(
        self,
        root_package: PackPackage,
        resolver: PackResolver,
        fallback_skills: SkillRegistry,
        provenance: dict[str, dict[str, Any]] | None = None,
    ) -> PackResolution:
        resolution = resolver.resolve_package(root_package)
        lock = self._lock_packages()
        to_install: list[PackPackage] = []
        for package in resolution.packages:
            existing = lock.get(package.manifest.id)
            target = self._target(package.manifest.id)
            if existing and existing.get("version") == package.manifest.version and target.is_dir():
                continue
            if target.exists():
                raise PackError(f"Pack already installed with different state: {package.manifest.id}")
            to_install.append(package)

        staging_root = self.root / ".staging" / uuid.uuid4().hex
        staged: list[tuple[PackPackage, Path, Path]] = []
        try:
            for package in to_install:
                self._reject_symlinks(package.path)
                staged_pack = staging_root / self._relative_id(package.manifest.id)
                staged_pack.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(package.path, staged_pack)
                self._materialize_skills(package, staged_pack, fallback_skills)
                staged.append((package, staged_pack, self._target(package.manifest.id)))
            self._validate_staged_skills(staged)
            for _package, staged_pack, target in staged:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_pack, target)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)

        for package in resolution.packages:
            installed = PackPackage(package.manifest, self._target(package.manifest.id))
            lock[package.manifest.id] = self._lock_record(installed, (provenance or {}).get(package.manifest.id))
        self._write_lock(lock)
        enabled = self.enabled_ids() | {package.manifest.id for package in resolution.packages}
        self._write_enabled(enabled)
        return resolution

    def enable(self, pack_id: str) -> set[str]:
        pack_id = canonical_pack_id(pack_id)
        catalog = self.installed_catalog()
        resolution = PackResolver(catalog).resolve(pack_id)
        enabled = self.enabled_ids() | {package.manifest.id for package in resolution.packages}
        self._write_enabled(enabled)
        return enabled

    def disable(self, pack_id: str) -> set[str]:
        pack_id = canonical_pack_id(pack_id)
        enabled = self.enabled_ids()
        if pack_id not in enabled:
            raise PackError(f"Pack is not enabled: {pack_id}")
        lock = self._lock_packages()
        dependents = sorted(
            item_id
            for item_id, item in lock.items()
            if item_id in enabled and pack_id in {dep.get("id") for dep in item.get("dependencies", [])}
        )
        if dependents:
            raise PackError(f"Cannot disable {pack_id}; enabled dependents: {', '.join(dependents)}")
        enabled.remove(pack_id)
        self._write_enabled(enabled)
        return enabled

    def uninstall(self, pack_id: str) -> None:
        pack_id = canonical_pack_id(pack_id)
        lock = self._lock_packages()
        if pack_id not in lock:
            raise PackError(f"Pack is not installed: {pack_id}")
        dependents = sorted(
            item_id for item_id, item in lock.items() if pack_id in {dep.get("id") for dep in item.get("dependencies", [])}
        )
        if dependents:
            raise PackError(f"Cannot uninstall {pack_id}; installed dependents: {', '.join(dependents)}")
        target = self._target(pack_id)
        if target.is_dir():
            shutil.rmtree(target)
        lock.pop(pack_id)
        self._write_lock(lock)
        self._write_enabled(self.enabled_ids() - {pack_id})

    def status(self) -> dict[str, Any]:
        enabled = self.enabled_ids()
        records = self._lock_packages()
        return {
            "root": str(self.root),
            "packs": [records[pack_id] | {"enabled": pack_id in enabled} for pack_id in sorted(records)],
        }

    def _materialize_skills(self, package: PackPackage, destination: Path, fallback: SkillRegistry) -> None:
        skills_root = destination / "skills"
        for reference in package.manifest.skills:
            source = self._skill_source(package, reference, fallback)
            errors = source.validate()
            if errors:
                raise PackError(f"Invalid Skill {reference} in {package.manifest.id}: {'; '.join(errors)}")
            target = skills_root / source.path.name
            if target.exists():
                installed = SkillPackage(source.manifest, target)
                if installed.manifest.id != source.manifest.id:
                    raise PackError(f"Skill path conflict in {package.manifest.id}: {target.name}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_symlinks(source.path)
            shutil.copytree(source.path, target)

    @staticmethod
    def _skill_source(package: PackPackage, reference: str, fallback: SkillRegistry) -> SkillPackage:
        local_candidates = (package.path / reference, package.path / "skills" / Path(reference).name)
        for candidate in local_candidates:
            try:
                candidate.resolve().relative_to(package.path.resolve())
            except ValueError as exc:
                raise PackError(f"Skill path escapes Pack root: {reference}") from exc
            entrypoint = candidate / "SKILL.md"
            if entrypoint.is_file():
                from .skill_registry import SkillManifest

                return SkillPackage(SkillManifest.from_file(entrypoint), candidate)
        try:
            return fallback.get(reference)
        except KeyError as exc:
            raise PackError(f"Missing Skill '{reference}' required by {package.manifest.id}") from exc

    @staticmethod
    def _reject_symlinks(root: Path) -> None:
        if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
            raise PackError(f"Pack and Skill sources may not contain symbolic links: {root}")

    def _validate_staged_skills(self, staged: list[tuple[PackPackage, Path, Path]]) -> None:
        roots = [target / "skills" for _package, _staged, target in staged if target.is_dir()]
        roots.extend(staged_pack / "skills" for _package, staged_pack, _target in staged)
        roots.extend(self.skill_roots())
        packages: list[SkillPackage] = []
        identities: dict[str, Path] = {}
        aliases: set[str] = set()
        for root in roots:
            for skill in SkillRegistry(root).list():
                previous = identities.get(skill.manifest.id)
                if previous is not None and previous.resolve() != skill.path.resolve():
                    raise PackError(f"Duplicate Skill id across installed Packs: {skill.manifest.id}")
                identities[skill.manifest.id] = skill.path
                aliases.update({skill.manifest.id, skill.manifest.name, skill.path.name})
                packages.append(skill)
        for skill in packages:
            missing = sorted(requirement for requirement in skill.manifest.requires if requirement not in aliases)
            if missing:
                raise PackError(f"Skill {skill.manifest.id} has missing dependencies: {', '.join(missing)}")

    def _target(self, pack_id: str) -> Path:
        return self.packs_dir / self._relative_id(pack_id)

    @staticmethod
    def _relative_id(pack_id: str) -> Path:
        canonical = canonical_pack_id(pack_id)
        if not _PACK_ID.fullmatch(canonical):
            raise PackError(f"Invalid Pack id: {pack_id}")
        return Path(*canonical.split("/"))

    def _lock_packages(self) -> dict[str, dict[str, Any]]:
        payload = read_json(self.lock_path, {"packs": []})
        values = payload.get("packs", []) if isinstance(payload, dict) else []
        return {
            canonical_pack_id(str(item["id"])): item
            for item in values
            if isinstance(item, dict) and item.get("id")
        }

    @staticmethod
    def _lock_record(package: PackPackage, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": package.manifest.id,
            "version": package.manifest.version,
            "sha256": _tree_digest(package.path),
            "dependencies": [requirement.to_dict() for requirement in package.manifest.pack_requirements],
            "skills": list(package.manifest.skills),
        }
        if provenance:
            record["source"] = provenance
        return record

    def _write_lock(self, records: dict[str, dict[str, Any]]) -> None:
        write_json(self.lock_path, {"schema_version": 1, "packs": [records[key] for key in sorted(records)]})

    def _write_enabled(self, enabled: set[str]) -> None:
        write_json(self.enabled_path, {"schema_version": 1, "packs": sorted(enabled)})


def _tree_digest(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
