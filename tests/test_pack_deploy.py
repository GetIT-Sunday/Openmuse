from __future__ import annotations

from pathlib import Path

import pytest

from smearglepaper.pack_deploy import GitHubPagesAdapter, RegistryDeploymentAdapter
from smearglepaper.pack_manager import PackError, PackPackage
from smearglepaper.pack_publisher import build_pack_archive, generate_signing_key, publish_signed_release, sign_pack_archive
from smearglepaper.skill_registry import PackManifest, SkillRegistry


def _published_registry(tmp_path: Path) -> Path:
    pack = tmp_path / "source" / "article"
    skill = pack / "skills" / "writer"
    skill.mkdir(parents=True)
    pack.joinpath("pack.yaml").write_text(
        "id: demo/article\n"
        "name: Demo Article\n"
        "version: 1.0.0\n"
        "requires:\n"
        "  harness: '>=0.2.0'\n"
        "  packs: []\n"
        "skills:\n"
        "  - skills/writer\n"
        "workflows: []\n",
        encoding="utf-8",
    )
    skill.joinpath("SKILL.md").write_text(
        "---\nname: writer\nid: demo.article.writer\nversion: 1.0.0\n---\n# Writer\n",
        encoding="utf-8",
    )
    package = PackPackage(PackManifest.from_file(pack / "pack.yaml"), pack)
    build = build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "dist")
    key = generate_signing_key(tmp_path / "author.pem", "demo-2026")
    signed = sign_pack_archive(build.archive, Path(key["private_key"]), key["key_id"])
    registry = tmp_path / "registry"
    publish_signed_release(signed.descriptor, registry, "Demo Registry")
    return registry


def test_github_pages_adapter_prepares_verified_site_and_workflow(tmp_path: Path) -> None:
    registry = _published_registry(tmp_path)
    output = tmp_path / "pages"
    workflow = tmp_path / ".github" / "workflows" / "deploy-pack-registry.yml"
    adapter: RegistryDeploymentAdapter = GitHubPagesAdapter()

    first = adapter.prepare(registry, output, workflow_path=workflow, site_path="pages")
    second = adapter.prepare(registry, output, workflow_path=workflow, site_path="pages")

    assert first.to_dict() == second.to_dict()
    assert (output / ".nojekyll").read_bytes() == b""
    assert (output / "index.json").read_bytes() == (registry / "index.json").read_bytes()
    assert len(first.archives) == 1
    assert first.archives[0].is_file()
    workflow_text = workflow.read_text(encoding="utf-8")
    assert "contents: read" in workflow_text
    assert "pages: write" in workflow_text
    assert "id-token: write" in workflow_text
    assert "workflow_dispatch:" in workflow_text
    assert "actions/checkout@v6" in workflow_text
    assert "actions/configure-pages@v5" in workflow_text
    assert "actions/upload-pages-artifact@v4" in workflow_text
    assert "actions/deploy-pages@v4" in workflow_text
    assert 'path: "pages"' in workflow_text


def test_github_pages_adapter_rejects_modified_or_missing_archive(tmp_path: Path) -> None:
    registry = _published_registry(tmp_path)
    archive = next((registry / "packs").rglob("*.zip"))
    archive.write_bytes(archive.read_bytes() + b"modified")

    with pytest.raises(PackError, match="SHA-256 mismatch"):
        GitHubPagesAdapter().prepare(registry, tmp_path / "pages")

    archive.unlink()
    with pytest.raises(PackError, match="does not exist"):
        GitHubPagesAdapter().prepare(registry, tmp_path / "pages")


def test_github_pages_adapter_does_not_overwrite_unmanaged_output(tmp_path: Path) -> None:
    registry = _published_registry(tmp_path)
    output = tmp_path / "pages"
    output.mkdir()
    output.joinpath("user-file.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(PackError, match="unmanaged"):
        GitHubPagesAdapter().prepare(registry, output)
    assert output.joinpath("user-file.txt").read_text(encoding="utf-8") == "keep"


def test_github_pages_workflow_requires_repository_relative_site_path(tmp_path: Path) -> None:
    registry = _published_registry(tmp_path)
    output = tmp_path / "pages"

    with pytest.raises(PackError, match="site path"):
        GitHubPagesAdapter().prepare(
            registry,
            output,
            workflow_path=tmp_path / "workflow.yml",
            site_path="../outside",
        )
    assert not output.exists()


def test_github_pages_adapter_rejects_symlinked_registry_path(tmp_path: Path) -> None:
    registry = _published_registry(tmp_path)
    packs = registry / "packs"
    moved = tmp_path / "outside-packs"
    packs.rename(moved)
    packs.symlink_to(moved, target_is_directory=True)

    with pytest.raises(PackError, match="symbolic links"):
        GitHubPagesAdapter().prepare(registry, tmp_path / "pages")
