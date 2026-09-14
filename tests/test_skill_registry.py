import json
from pathlib import Path

import pytest

from smearglepaper.pack_manager import (
    InstalledPackStore,
    PackCatalog,
    PackError,
    PackPackage,
    PackResolver,
    version_satisfies,
)
from smearglepaper.skill_adapter import AdapterRegistry, SkillRequest, SkillResult
from smearglepaper.skill_registry import PackManifest, SkillManifest, SkillRegistry, discover_packs, export_pack
from smearglepaper.tools import installed_capabilities


def test_registry_discovers_legacy_and_manifest_skills() -> None:
    registry = SkillRegistry(Path("skills"))
    packages = registry.list()
    assert packages
    package = registry.get("write-paper-wechat")
    assert package.manifest.id == "autowechat.write-paper-wechat"
    assert package.validate() == []


def test_pack_is_a_composition_of_portable_skills(tmp_path: Path) -> None:
    packs = discover_packs(Path("packs"))
    pack, _ = next(item for item in packs if item[0].id == "autowechat/research-to-wechat")
    assert "write-paper-wechat" in pack.skills
    exported = export_pack(Path("packs"), pack.id, SkillRegistry(Path("skills")), tmp_path)
    assert exported
    assert all((path / "SKILL.md").is_file() for path in exported)
    assert (tmp_path / "research-to-wechat" / "pack.yaml").is_file()
    assert (tmp_path / "research-to-wechat" / "workflows" / "paper-to-wechat.yaml").is_file()


def test_skill_manifest_defaults_support_portable_legacy_files(tmp_path: Path) -> None:
    skill = tmp_path / "example"
    skill.mkdir()
    entrypoint = skill / "SKILL.md"
    entrypoint.write_text("# Example\n", encoding="utf-8")
    manifest = SkillManifest.from_file(entrypoint)
    assert manifest.id == "local.example"
    assert manifest.name == "example"


def test_adapter_registry_keeps_runtime_adapters_replaceable() -> None:
    class Adapter:
        skill_id = "example.skill"

        def run(self, request, services):
            return SkillResult(request.skill_id, "completed")

    registry = AdapterRegistry()
    registry.register(Adapter())
    assert registry.get("example.skill").run(SkillRequest("example.skill"), None).status == "completed"


def test_installed_capabilities_filter_tools_by_valid_skills() -> None:
    tools, catalog = installed_capabilities()
    names = {str(tool["function"]["name"]) for tool in tools}
    assert "run_writing_agent" in names
    assert "autowechat.write-paper-wechat" in catalog


def _write_skill(root: Path, name: str, skill_id: str, requires: tuple[str, ...] = ()) -> None:
    skill = root / "skills" / name
    skill.mkdir(parents=True)
    dependencies = "\n".join(f"  - {item}" for item in requires)
    skill.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\nid: {skill_id}\nversion: 1.0.0\nrequires:\n{dependencies}\n---\n# {name}\n",
        encoding="utf-8",
    )


def _write_pack(
    root: Path,
    pack_id: str,
    version: str = "1.0.0",
    dependencies: tuple[tuple[str, str], ...] = (),
    skills: tuple[str, ...] = (),
) -> PackPackage:
    pack = root / pack_id.split("/")[-1]
    pack.mkdir(parents=True)
    requires = "\n".join(f"    - id: {item_id}\n      version: '{constraint}'" for item_id, constraint in dependencies)
    skill_lines = "\n".join(f"  - {item}" for item in skills)
    pack.joinpath("pack.yaml").write_text(
        f"id: {pack_id}\nname: {pack_id}\nversion: {version}\n"
        f"requires:\n  harness: '>=0.2.0'\n  packs:\n{requires}\nskills:\n{skill_lines}\n",
        encoding="utf-8",
    )
    return PackPackage(PackManifest.from_file(pack / "pack.yaml"), pack)


def test_pack_version_constraints() -> None:
    assert version_satisfies("1.2.3", "*")
    assert version_satisfies("1.2.3", "1.2.3")
    assert version_satisfies("1.2.3", ">=1.0.0")
    assert version_satisfies("1.9.0", "^1.2.0")
    assert not version_satisfies("2.0.0", "^1.2.0")
    assert version_satisfies("0.2.9", "^0.2.1")
    assert not version_satisfies("0.3.0", "^0.2.1")


def test_pack_resolver_returns_dependencies_before_root(tmp_path: Path) -> None:
    dependency = _write_pack(tmp_path, "demo/base", "1.2.0")
    root = _write_pack(tmp_path, "demo/article", dependencies=(("demo/base", "^1.0.0"),))
    resolution = PackResolver(PackCatalog((root, dependency)), "0.2.0").resolve_package(root)
    assert [item.manifest.id for item in resolution.packages] == ["demo/base", "demo/article"]


def test_pack_resolver_rejects_missing_and_circular_dependencies(tmp_path: Path) -> None:
    root = _write_pack(tmp_path, "demo/article", dependencies=(("demo/missing", "*"),))
    with pytest.raises(PackError, match="Missing Pack demo/missing"):
        PackResolver(PackCatalog((root,)), "0.2.0").resolve_package(root)

    first = _write_pack(tmp_path / "cycle-a", "demo/a", dependencies=(("demo/b", "*"),))
    second = _write_pack(tmp_path / "cycle-b", "demo/b", dependencies=(("demo/a", "*"),))
    with pytest.raises(PackError, match="Circular Pack dependency"):
        PackResolver(PackCatalog((first, second)), "0.2.0").resolve_package(first)


def test_pack_resolver_deduplicates_shared_dependencies_and_rejects_conflicts(tmp_path: Path) -> None:
    base_v1 = _write_pack(tmp_path / "base-v1", "demo/base", "1.2.0")
    base_v2 = _write_pack(tmp_path / "base-v2", "demo/base", "2.0.0")
    addon = _write_pack(tmp_path / "addon", "demo/addon", dependencies=(("demo/base", "^1.0.0"),))
    compatible = _write_pack(
        tmp_path / "compatible",
        "demo/compatible",
        dependencies=(("demo/base", "^1.0.0"), ("demo/addon", "*")),
    )
    catalog = PackCatalog((base_v1, base_v2, addon, compatible))
    resolution = PackResolver(catalog, "0.2.0").resolve_package(compatible)
    assert [item.manifest.id for item in resolution.packages] == ["demo/base", "demo/addon", "demo/compatible"]

    conflicting = _write_pack(tmp_path / "conflicting", "demo/conflicting", dependencies=(("demo/base", "^2.0.0"),))
    root = _write_pack(
        tmp_path / "root",
        "demo/root",
        dependencies=(("demo/addon", "*"), ("demo/conflicting", "*")),
    )
    conflict_catalog = PackCatalog((base_v1, base_v2, addon, conflicting, root))
    with pytest.raises(PackError, match="Conflicting requirements for demo/base"):
        PackResolver(conflict_catalog, "0.2.0").resolve_package(root)


def test_pack_resolver_backtracks_to_globally_compatible_version(tmp_path: Path) -> None:
    base_v1 = _write_pack(tmp_path / "base-v1", "demo/base", "1.2.0")
    base_v2 = _write_pack(tmp_path / "base-v2", "demo/base", "2.0.0")
    addon = _write_pack(tmp_path / "addon", "demo/addon", dependencies=(("demo/base", "^1.0.0"),))
    root = _write_pack(
        tmp_path / "root",
        "demo/root",
        dependencies=(("demo/base", ">=1.0.0"), ("demo/addon", "*")),
    )
    resolution = PackResolver(PackCatalog((base_v1, base_v2, addon, root)), "0.2.0").resolve_package(root)
    selected = {item.manifest.id: item.manifest.version for item in resolution.packages}
    assert selected["demo/base"] == "1.2.0"


def test_pack_install_materializes_skills_and_writes_lock(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    fallback_project = tmp_path / "builtin"
    fallback_root = fallback_project / "skills"
    _write_skill(fallback_project, "reader", "demo.reader")
    package = _write_pack(sources, "demo/article", skills=("reader",))
    store = InstalledPackStore(tmp_path / "project" / ".aigc")

    resolution = store.install(
        package,
        PackResolver(PackCatalog((package,)), "0.2.0"),
        SkillRegistry(fallback_root),
    )

    assert resolution.root == "demo/article"
    assert (store.packs_dir / "demo" / "article" / "skills" / "reader" / "SKILL.md").is_file()
    lock = json.loads(store.lock_path.read_text(encoding="utf-8"))
    assert lock["packs"][0]["id"] == "demo/article"
    assert store.enabled_ids() == {"demo/article"}


def test_enabled_pack_skills_are_discovered_by_harness(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "sources"
    package = _write_pack(source, "demo/article", skills=("writer",))
    _write_skill(package.path, "writer", "autowechat.write-paper-wechat")
    store = InstalledPackStore(project / ".aigc")
    store.install(package, PackResolver(PackCatalog((package,)), "0.2.0"), SkillRegistry(project / "skills"))

    tools, catalog = installed_capabilities(project)
    assert "run_writing_agent" in {str(tool["function"]["name"]) for tool in tools}
    assert "autowechat.write-paper-wechat" in catalog


def test_pack_lifecycle_protects_enabled_dependencies(tmp_path: Path) -> None:
    dependency = _write_pack(tmp_path / "sources-a", "demo/base")
    root = _write_pack(tmp_path / "sources-b", "demo/article", dependencies=(("demo/base", "*"),))
    catalog = PackCatalog((root, dependency))
    store = InstalledPackStore(tmp_path / ".aigc")
    store.install(root, PackResolver(catalog, "0.2.0"), SkillRegistry(tmp_path / "skills"))

    assert store.enabled_ids() == {"demo/base", "demo/article"}
    with pytest.raises(PackError, match="enabled dependents: demo/article"):
        store.disable("demo/base")
    with pytest.raises(PackError, match="installed dependents: demo/article"):
        store.uninstall("demo/base")

    store.disable("demo/article")
    store.disable("demo/base")
    store.uninstall("demo/article")
    store.uninstall("demo/base")
    assert store.status()["packs"] == []


def test_pack_install_rejects_escaping_skill_path(tmp_path: Path) -> None:
    package = _write_pack(tmp_path / "sources", "demo/unsafe", skills=("../outside",))
    _write_skill(package.path.parent, "outside", "demo.outside")
    store = InstalledPackStore(tmp_path / ".aigc")

    with pytest.raises(PackError, match="escapes Pack root"):
        store.install(package, PackResolver(PackCatalog((package,)), "0.2.0"), SkillRegistry(tmp_path / "none"))
    assert not store.lock_path.exists()


def test_pack_validation_rejects_missing_workflow_resource(tmp_path: Path) -> None:
    package = _write_pack(tmp_path, "demo/article")
    package.path.joinpath("pack.yaml").write_text(
        package.path.joinpath("pack.yaml").read_text(encoding="utf-8")
        + "workflows:\n  - workflows/missing.yaml\n",
        encoding="utf-8",
    )
    invalid = PackPackage(PackManifest.from_file(package.path / "pack.yaml"), package.path)
    assert invalid.validate("0.2.0") == ["missing workflow: workflows/missing.yaml"]
