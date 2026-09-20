from __future__ import annotations

import base64
import io
import json
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from smearglepaper.pack_manager import InstalledPackStore, PackCatalog, PackError, PackResolver
from smearglepaper.pack_registry import (
    PackRegistryClient,
    RegistryConfig,
    RegistryConfigStore,
    RegistryIndex,
    RegistryResolver,
)
from smearglepaper.skill_registry import SkillRegistry


def _archive(pack_id: str, version: str = "1.0.0", dependencies: tuple[tuple[str, str], ...] = ()) -> bytes:
    requires = "\n".join(f"    - id: {item_id}\n      version: '{constraint}'" for item_id, constraint in dependencies)
    manifest = (
        f"id: {pack_id}\nname: Test Pack\nversion: {version}\n"
        f"requires:\n  harness: '>=0.2.0'\n  packs:\n{requires}\n"
        "skills:\n  - skills/writer\nworkflows: []\n"
    )
    skill = f"---\nname: writer\nid: {pack_id.replace('/', '.')}.writer\nversion: {version}\n---\n# Writer\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package/pack.yaml", manifest)
        archive.writestr("package/skills/writer/SKILL.md", skill)
    return output.getvalue()


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private_key, base64.b64encode(public_key).decode("ascii")


def _release(
    private_key: Ed25519PrivateKey,
    pack_id: str,
    archive: bytes,
    version: str = "1.0.0",
    dependencies: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    return {
        "version": version,
        "url": f"packs/{pack_id.replace('/', '-')}-{version}.zip",
        "sha256": sha256(archive).hexdigest(),
        "signature": base64.b64encode(private_key.sign(archive)).decode("ascii"),
        "key_id": "test-2026",
        "harness": ">=0.2.0",
        "dependencies": [{"id": item_id, "version": constraint} for item_id, constraint in dependencies],
    }


def _index(packs: list[tuple[str, list[dict[str, object]]]]) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "name": "test",
            "packs": [{"id": pack_id, "versions": versions} for pack_id, versions in packs],
        }
    ).encode("utf-8")


def test_signed_registry_downloads_dependency_graph_and_installs(tmp_path: Path) -> None:
    private_key, public_key = _key_pair()
    base_archive = _archive("demo/base")
    root_archive = _archive("demo/article", dependencies=(("demo/base", "^1.0.0"),))
    index = _index(
        [
            ("demo/base", [_release(private_key, "demo/base", base_archive)]),
            (
                "demo/article",
                [_release(private_key, "demo/article", root_archive, dependencies=(("demo/base", "^1.0.0"),))],
            ),
        ]
    )
    resources = {
        "https://packs.example/index.json": index,
        "https://packs.example/packs/demo-base-1.0.0.zip": base_archive,
        "https://packs.example/packs/demo-article-1.0.0.zip": root_archive,
    }
    calls: list[str] = []

    def download(url: str, _maximum: int) -> bytes:
        calls.append(url)
        return resources[url]

    client = PackRegistryClient(
        RegistryConfig("test", "https://packs.example/index.json", {"test-2026": public_key}),
        tmp_path / "cache",
        download,
    )

    remote = client.download("demo/article", "^1.0.0", "0.2.0")
    assert [item.pack_id for item in remote.releases] == ["demo/base", "demo/article"]
    client.download("demo/article", "^1.0.0", "0.2.0")
    assert calls.count("https://packs.example/packs/demo-base-1.0.0.zip") == 1
    assert calls.count("https://packs.example/packs/demo-article-1.0.0.zip") == 1
    catalog = PackCatalog(remote.packages)
    store = InstalledPackStore(tmp_path / ".aigc")
    resolution = store.install(
        catalog.get(remote.root),
        PackResolver(catalog, "0.2.0"),
        SkillRegistry(tmp_path / "skills"),
        remote.provenance(client.config),
    )
    assert [item.manifest.id for item in resolution.packages] == ["demo/base", "demo/article"]
    source = store.status()["packs"][1]["source"]
    assert source["registry"] == "test"
    assert source["signature_key_id"] == "test-2026"


def test_registry_rejects_hash_and_signature_tampering(tmp_path: Path) -> None:
    private_key, public_key = _key_pair()
    original = _archive("demo/article")
    tampered = original + b"tampered"
    release = _release(private_key, "demo/article", original)
    index = _index([("demo/article", [release])])
    config = RegistryConfig("test", "https://packs.example/index.json", {"test-2026": public_key})

    hash_client = PackRegistryClient(
        config,
        tmp_path / "hash-cache",
        lambda url, _maximum: index if url.endswith("index.json") else tampered,
    )
    with pytest.raises(PackError, match="SHA-256 mismatch"):
        hash_client.download("demo/article", "*", "0.2.0")

    signed_other = _archive("demo/other")
    release["sha256"] = sha256(tampered).hexdigest()
    release["signature"] = base64.b64encode(private_key.sign(signed_other)).decode("ascii")
    signature_index = _index([("demo/article", [release])])
    signature_client = PackRegistryClient(
        config,
        tmp_path / "signature-cache",
        lambda url, _maximum: signature_index if url.endswith("index.json") else tampered,
    )
    with pytest.raises(PackError, match="Invalid signature"):
        signature_client.download("demo/article", "*", "0.2.0")


def test_registry_rejects_signed_archive_path_traversal(tmp_path: Path) -> None:
    private_key, public_key = _key_pair()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../escape.txt", "unsafe")
        archive.writestr("pack.yaml", "id: demo/unsafe\nversion: 1.0.0\nskills: []\n")
    payload = output.getvalue()
    index = _index([("demo/unsafe", [_release(private_key, "demo/unsafe", payload)])])
    client = PackRegistryClient(
        RegistryConfig("test", "https://packs.example/index.json", {"test-2026": public_key}),
        tmp_path / "cache",
        lambda url, _maximum: index if url.endswith("index.json") else payload,
    )

    with pytest.raises(PackError, match="Unsafe path"):
        client.download("demo/unsafe", "*", "0.2.0")
    assert not (tmp_path / "escape.txt").exists()


def test_registry_config_requires_https_and_pinned_key(tmp_path: Path) -> None:
    _private_key, public_key = _key_pair()
    store = RegistryConfigStore(tmp_path / "registries.json")
    store.add(RegistryConfig("official", "https://packs.example/index.json", {"official-2026": public_key}))
    assert store.get().name == "official"
    _new_private_key, new_public_key = _key_pair()
    updated = store.trust_key("official", "official-2027", new_public_key)
    assert set(updated.trusted_keys) == {"official-2026", "official-2027"}
    store.remove("official")
    assert store.list() == []

    with pytest.raises(PackError, match="must use HTTPS"):
        store.add(RegistryConfig("unsafe", "http://packs.example/index.json", {"official-2026": public_key}))
    with pytest.raises(PackError, match="pinned Ed25519"):
        store.add(RegistryConfig("unsigned", "https://packs.example/index.json", {}))


def test_registry_index_search_and_harness_compatibility() -> None:
    private_key, _public_key = _key_pair()
    archive = _archive("demo/article")
    release = _release(private_key, "demo/article", archive)
    release["harness"] = ">=9.0.0"
    index = RegistryIndex.from_bytes(_index([("demo/article", [release])]))

    assert index.search("article") == [{"id": "demo/article", "versions": ["1.0.0"]}]
    with pytest.raises(PackError, match="supports AIGC Harness"):
        RegistryResolver(index, "0.2.0").resolve("demo/article")


def test_remote_resolver_backtracks_to_globally_compatible_version() -> None:
    private_key, _public_key = _key_pair()
    base_v1 = _archive("demo/base", "1.2.0")
    base_v2 = _archive("demo/base", "2.0.0")
    addon = _archive("demo/addon", dependencies=(("demo/base", "^1.0.0"),))
    root = _archive("demo/root", dependencies=(("demo/base", ">=1.0.0"), ("demo/addon", "*")))
    index = RegistryIndex.from_bytes(
        _index(
            [
                (
                    "demo/base",
                    [
                        _release(private_key, "demo/base", base_v2, "2.0.0"),
                        _release(private_key, "demo/base", base_v1, "1.2.0"),
                    ],
                ),
                ("demo/addon", [_release(private_key, "demo/addon", addon, dependencies=(("demo/base", "^1.0.0"),))]),
                (
                    "demo/root",
                    [
                        _release(
                            private_key,
                            "demo/root",
                            root,
                            dependencies=(("demo/base", ">=1.0.0"), ("demo/addon", "*")),
                        )
                    ],
                ),
            ]
        )
    )
    resolution = RegistryResolver(index, "0.2.0").resolve("demo/root")
    selected = {item.pack_id: item.version for item in resolution.releases}
    assert selected["demo/base"] == "1.2.0"
