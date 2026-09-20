from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from smearglepaper.pack_manager import InstalledPackStore, PackCatalog, PackError, PackPackage, PackResolver
from smearglepaper.pack_publisher import (
    build_pack_archive,
    generate_signing_key,
    publish_signed_release,
    sign_pack_archive,
)
from smearglepaper.pack_registry import PackRegistryClient, RegistryConfig
from smearglepaper.skill_registry import PackManifest, SkillRegistry


def _source_pack(root: Path) -> PackPackage:
    pack = root / "article"
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
    return PackPackage(PackManifest.from_file(pack / "pack.yaml"), pack)


def test_pack_build_is_reproducible_and_self_contained(tmp_path: Path) -> None:
    package = _source_pack(tmp_path / "source")
    first = build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "first")
    second = build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "second")

    assert first.sha256 == second.sha256
    assert first.archive.read_bytes() == second.archive.read_bytes()
    assert first.archive.name == "demo-article-1.0.0.zip"


def test_build_refuses_environment_files_and_private_keys(tmp_path: Path) -> None:
    package = _source_pack(tmp_path / "source")
    package.path.joinpath(".env").write_text("TOKEN=secret\n", encoding="utf-8")
    with pytest.raises(PackError, match="environment secrets"):
        build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "dist")

    package.path.joinpath(".env").unlink()
    package.path.joinpath("secret.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    with pytest.raises(PackError, match="private key"):
        build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "dist")


def test_keygen_uses_private_permissions_and_rejects_overwrite(tmp_path: Path) -> None:
    private_key = tmp_path / "author.pem"
    result = generate_signing_key(private_key, "demo-2026")
    assert result["public_key"]
    if os.name == "posix":
        assert private_key.stat().st_mode & 0o077 == 0
    with pytest.raises(PackError, match="already exists"):
        generate_signing_key(private_key, "demo-2026")


def test_build_sign_publish_and_remote_install_round_trip(tmp_path: Path) -> None:
    package = _source_pack(tmp_path / "source")
    build = build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "dist")
    key = generate_signing_key(tmp_path / "author.pem", "demo-2026")
    signed = sign_pack_archive(build.archive, Path(key["private_key"]), key["key_id"])
    published = publish_signed_release(signed.descriptor, tmp_path / "registry", "Demo Registry")

    index_path = Path(published["index"])
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["packs"][0]["id"] == "demo/article"
    assert "public_key" not in index

    resources = {
        "https://packs.example/index.json": index_path.read_bytes(),
        "https://packs.example/packs/demo/article/1.0.0.zip": Path(published["archive"]).read_bytes(),
    }
    client = PackRegistryClient(
        RegistryConfig("demo", "https://packs.example/index.json", {key["key_id"]: key["public_key"]}),
        tmp_path / "consumer-cache",
        lambda url, _maximum: resources[url],
    )
    remote = client.download("demo/article", "*", "0.2.0")
    catalog = PackCatalog(remote.packages)
    store = InstalledPackStore(tmp_path / "consumer" / ".aigc")
    store.install(
        catalog.get("demo/article"),
        PackResolver(catalog, "0.2.0"),
        SkillRegistry(tmp_path / "consumer" / "skills"),
        remote.provenance(client.config),
    )
    assert (store.packs_dir / "demo" / "article" / "skills" / "writer" / "SKILL.md").is_file()


def test_publish_rejects_modified_archive(tmp_path: Path) -> None:
    package = _source_pack(tmp_path / "source")
    build = build_pack_archive(package, SkillRegistry(tmp_path / "none"), tmp_path / "dist")
    key = generate_signing_key(tmp_path / "author.pem", "demo-2026")
    signed = sign_pack_archive(build.archive, Path(key["private_key"]), key["key_id"])
    build.archive.write_bytes(build.archive.read_bytes() + b"modified")

    with pytest.raises(PackError, match="SHA-256"):
        publish_signed_release(signed.descriptor, tmp_path / "registry")
