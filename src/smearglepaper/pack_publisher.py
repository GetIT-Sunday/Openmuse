"""Reproducible build, signing, and static publishing for Capability Packs."""
from __future__ import annotations

import base64
import hmac
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .pack_manager import PackError, PackPackage, harness_version, parse_version
from .pack_registry import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_FILES,
    MAX_EXTRACTED_BYTES,
    RegistryIndex,
    RegistryRelease,
    _extract_pack_zip,
    _find_package_root,
)
from .skill_registry import PackManifest, SkillManifest, SkillPackage, SkillRegistry, export_pack_package
from .storage import write_json

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_IGNORED_NAMES = {".DS_Store", ".git", "__pycache__"}


@dataclass(frozen=True)
class PackBuild:
    pack_id: str
    version: str
    archive: Path
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.pack_id,
            "version": self.version,
            "archive": str(self.archive),
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SignedRelease:
    pack_id: str
    version: str
    descriptor: Path
    public_key: str
    key_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.pack_id,
            "version": self.version,
            "descriptor": str(self.descriptor),
            "key_id": self.key_id,
            "public_key": self.public_key,
        }


def generate_signing_key(path: Path, key_id: str) -> dict[str, str]:
    """Create a permission-restricted Ed25519 PKCS8 key and return its raw public key."""
    if not key_id.strip():
        raise PackError("Signing key id cannot be empty")
    path = path.expanduser()
    if path.exists():
        raise PackError(f"Signing key already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(pem)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    public_key = _public_key_base64(private_key.public_key())
    return {"key_id": key_id, "private_key": str(path), "public_key": public_key}


def build_pack_archive(package: PackPackage, skills: SkillRegistry, destination: Path) -> PackBuild:
    """Materialize a Pack and write a reproducible, self-contained ZIP archive."""
    errors = package.validate(harness_version(), require_compatible=False)
    if errors:
        raise PackError(f"Invalid Pack {package.manifest.id}@{package.manifest.version}: {'; '.join(errors)}")
    _reject_source_symlinks(package.path)
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{package.manifest.id.replace('/', '-')}-{package.manifest.version}.zip"
    target = destination / filename
    temporary = destination / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        with tempfile.TemporaryDirectory(prefix="aigc-pack-build-") as temporary_directory:
            export_root = Path(temporary_directory) / "export"
            export_pack_package(package.manifest, package.path, skills, export_root)
            materialized = export_root / package.path.name
            _write_deterministic_zip(materialized, temporary, package.manifest.id.split("/", 1)[1])
        archive_payload = temporary.read_bytes()
        built_manifest = _inspect_archive(archive_payload)
        if (built_manifest.id, built_manifest.version) != (package.manifest.id, package.manifest.version):
            raise PackError("Built Pack identity changed during materialization")
        digest = sha256(archive_payload).hexdigest()
        if target.exists():
            if hmac.compare_digest(sha256(target.read_bytes()).hexdigest(), digest):
                return PackBuild(package.manifest.id, package.manifest.version, target, digest)
            raise PackError(f"Pack archive already exists with different content: {target}")
        os.replace(temporary, target)
        return PackBuild(package.manifest.id, package.manifest.version, target, digest)
    finally:
        temporary.unlink(missing_ok=True)


def sign_pack_archive(
    archive: Path,
    private_key_path: Path,
    key_id: str,
    descriptor: Path | None = None,
) -> SignedRelease:
    """Sign exact archive bytes and emit a Registry release descriptor."""
    if not key_id.strip():
        raise PackError("Signing key id cannot be empty")
    archive = archive.resolve()
    payload = archive.read_bytes()
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise PackError(f"Pack archive exceeds {MAX_ARCHIVE_BYTES} bytes")
    manifest = _inspect_archive(payload)
    private_key = _load_private_key(private_key_path)
    signature = private_key.sign(payload)
    encoded_public_key = _public_key_base64(private_key.public_key())
    relative_url = f"packs/{manifest.id}/{manifest.version}.zip"
    release = {
        "version": manifest.version,
        "url": relative_url,
        "sha256": sha256(payload).hexdigest(),
        "signature": base64.b64encode(signature).decode("ascii"),
        "key_id": key_id,
        "harness": manifest.harness_requirement,
        "dependencies": [item.to_dict() for item in manifest.pack_requirements],
    }
    RegistryRelease.from_dict(manifest.id, release)
    target = descriptor or archive.with_suffix(".release.json")
    release_descriptor = {
        "schema_version": 1,
        "id": manifest.id,
        "archive": archive.name,
        "public_key": {"algorithm": "ed25519", "key_id": key_id, "value": encoded_public_key},
        "release": release,
    }
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing != release_descriptor:
            raise PackError(f"Release descriptor already exists with different content: {target}")
    else:
        write_json(target, release_descriptor)
    return SignedRelease(manifest.id, manifest.version, target, encoded_public_key, key_id)


def publish_signed_release(descriptor: Path, registry_root: Path, registry_name: str = "AIGC Pack Registry") -> dict[str, Any]:
    """Verify and merge one signed release into a deployable static Registry."""
    loaded = json.loads(descriptor.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
        raise PackError("Unsupported signed release descriptor schema")
    pack_id = str(loaded.get("id", ""))
    release_value = loaded.get("release")
    public_key_value = loaded.get("public_key")
    if not isinstance(release_value, dict) or not isinstance(public_key_value, dict):
        raise PackError("Signed release descriptor is incomplete")
    release = RegistryRelease.from_dict(pack_id, release_value)
    if public_key_value.get("algorithm") != "ed25519" or public_key_value.get("key_id") != release.key_id:
        raise PackError("Signed release public key metadata does not match the release")
    public_key = _decode_public_key(str(public_key_value.get("value", "")))
    archive_name = PurePosixPath(str(loaded.get("archive", "")))
    if len(archive_name.parts) != 1 or archive_name.is_absolute() or ".." in archive_name.parts:
        raise PackError("Release archive must be next to its descriptor")
    archive = descriptor.parent / archive_name.name
    payload = archive.read_bytes()
    digest = sha256(payload).hexdigest()
    if not hmac.compare_digest(digest, release.archive_sha256):
        raise PackError("Release archive SHA-256 does not match its descriptor")
    try:
        public_key.verify(base64.b64decode(release.signature, validate=True), payload)
    except (InvalidSignature, ValueError) as exc:
        raise PackError("Release archive signature is invalid") from exc
    manifest = _inspect_archive(payload)
    _validate_release_manifest(manifest, release)

    relative_archive = PurePosixPath(release.archive_url)
    if relative_archive.is_absolute() or ".." in relative_archive.parts or ":" in release.archive_url or "\\" in release.archive_url:
        raise PackError("Published archive URL must be a safe relative path")
    archive_target = registry_root.joinpath(*relative_archive.parts)
    if archive_target.exists() and not hmac.compare_digest(sha256(archive_target.read_bytes()).hexdigest(), digest):
        raise PackError(f"Registry archive already exists with different content: {archive_target}")
    if not archive_target.exists():
        _atomic_copy(archive, archive_target)

    index_path = registry_root / "index.json"
    index = _read_or_create_index(index_path, registry_name)
    _merge_release(index, release)
    write_json(index_path, index)
    return {
        "id": release.pack_id,
        "version": release.version,
        "registry": str(registry_root),
        "index": str(index_path),
        "archive": str(archive_target),
        "key_id": release.key_id,
    }


def _write_deterministic_zip(source: Path, target: Path, root_name: str) -> None:
    files = [path for path in source.rglob("*") if path.is_file() and not _ignored(path, source)]
    if len(files) > MAX_ARCHIVE_FILES:
        raise PackError(f"Pack contains more than {MAX_ARCHIVE_FILES} files")
    total_size = sum(path.stat().st_size for path in files)
    if total_size > MAX_EXTRACTED_BYTES:
        raise PackError(f"Pack content exceeds {MAX_EXTRACTED_BYTES} bytes")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(source).as_posix()):
            _reject_sensitive_file(path)
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(f"{root_name}/{relative}", _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    if target.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackError(f"Built Pack archive exceeds {MAX_ARCHIVE_BYTES} bytes")


def _ignored(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return any(part in _IGNORED_NAMES for part in relative.parts) or path.suffix == ".pyc"


def _reject_sensitive_file(path: Path) -> None:
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        raise PackError(f"Refusing to package environment secrets: {path.name}")
    payload = path.read_bytes()
    if b"-----BEGIN PRIVATE KEY-----" in payload or b"-----BEGIN OPENSSH PRIVATE KEY-----" in payload:
        raise PackError(f"Refusing to package a private key: {path.name}")


def _reject_source_symlinks(root: Path) -> None:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise PackError(f"Pack sources may not contain symbolic links: {root}")


def _inspect_archive(payload: bytes) -> PackManifest:
    with tempfile.TemporaryDirectory(prefix="aigc-pack-inspect-") as temporary_directory:
        destination = Path(temporary_directory) / "archive"
        _extract_pack_zip(payload, destination)
        package_root = _find_package_root(destination)
        manifest = PackManifest.from_file(package_root / "pack.yaml")
        errors = PackPackage(manifest, package_root).validate(harness_version(), require_compatible=False)
        if errors:
            raise PackError(f"Invalid Pack archive {manifest.id}@{manifest.version}: {'; '.join(errors)}")
        _validate_pack_skills(package_root, manifest)
        return manifest


def _validate_pack_skills(root: Path, manifest: PackManifest) -> None:
    packages: list[SkillPackage] = []
    for reference in manifest.skills:
        candidates = (root / reference, root / "skills" / Path(reference).name)
        skill_root = next((candidate for candidate in candidates if (candidate / "SKILL.md").is_file()), None)
        if skill_root is None:
            raise PackError(f"Pack archive is missing Skill: {reference}")
        package = SkillPackage(SkillManifest.from_file(skill_root / "SKILL.md"), skill_root)
        errors = package.validate()
        if errors:
            raise PackError(f"Invalid Skill {package.manifest.id}: {'; '.join(errors)}")
        packages.append(package)
    ids = [package.manifest.id for package in packages]
    if len(ids) != len(set(ids)):
        raise PackError("Pack archive contains duplicate Skill ids")
    aliases = {
        alias
        for package in packages
        for alias in (package.manifest.id, package.manifest.name, package.path.name)
    }
    for package in packages:
        missing = sorted(requirement for requirement in package.manifest.requires if requirement not in aliases)
        if missing:
            raise PackError(f"Skill {package.manifest.id} has missing dependencies: {', '.join(missing)}")


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    path = path.expanduser()
    if path.is_symlink() or not path.is_file():
        raise PackError(f"Signing key does not exist or is a symbolic link: {path}")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PackError("Signing key permissions are too open; use chmod 600")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (TypeError, ValueError) as exc:
        raise PackError("Signing key must be an unencrypted PEM private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise PackError("Signing key must be Ed25519")
    return key


def _public_key_base64(key: Ed25519PublicKey) -> str:
    payload = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(payload).decode("ascii")


def _decode_public_key(value: str) -> Ed25519PublicKey:
    try:
        payload = base64.b64decode(value, validate=True)
        return Ed25519PublicKey.from_public_bytes(payload)
    except (ValueError, TypeError) as exc:
        raise PackError("Release descriptor contains an invalid Ed25519 public key") from exc


def _validate_release_manifest(manifest: PackManifest, release: RegistryRelease) -> None:
    dependencies = tuple((item.id, item.version) for item in manifest.pack_requirements)
    release_dependencies = tuple((item.id, item.version) for item in release.dependencies)
    if (
        manifest.id != release.pack_id
        or manifest.version != release.version
        or manifest.harness_requirement != release.harness_requirement
        or dependencies != release_dependencies
    ):
        raise PackError("Signed archive manifest does not match the release descriptor")


def _read_or_create_index(path: Path, name: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "name": name, "packs": []}
    payload = path.read_bytes()
    RegistryIndex.from_bytes(payload)
    loaded = json.loads(payload.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise PackError("Registry index must be a JSON object")
    return loaded


def _merge_release(index: dict[str, Any], release: RegistryRelease) -> None:
    packs = index.setdefault("packs", [])
    if not isinstance(packs, list):
        raise PackError("Registry index 'packs' must be a list")
    pack = next((item for item in packs if isinstance(item, dict) and item.get("id") == release.pack_id), None)
    if pack is None:
        pack = {"id": release.pack_id, "versions": []}
        packs.append(pack)
    versions = pack.setdefault("versions", [])
    if not isinstance(versions, list):
        raise PackError(f"Registry versions must be a list: {release.pack_id}")
    release_value = {
        "version": release.version,
        "url": release.archive_url,
        "sha256": release.archive_sha256,
        "signature": release.signature,
        "key_id": release.key_id,
        "harness": release.harness_requirement,
        "dependencies": release.requirement_dicts(),
    }
    existing = next((item for item in versions if isinstance(item, dict) and item.get("version") == release.version), None)
    if existing is not None and existing != release_value:
        raise PackError(f"Registry already contains a different {release.pack_id}@{release.version}")
    if existing is None:
        versions.append(release_value)
    versions.sort(key=lambda item: parse_version(str(item["version"])), reverse=True)
    packs.sort(key=lambda item: str(item["id"]))


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
