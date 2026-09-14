"""Signed remote registry support for AIGC Capability Packs."""
from __future__ import annotations

import base64
import binascii
import hmac
import io
import json
import os
import shutil
import stat
import unicodedata
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .collector import ssl_context
from .pack_manager import PackCatalog, PackError, PackPackage, PackResolver, version_satisfies
from .skill_registry import PackManifest, PackRequirement, canonical_pack_id
from .storage import read_json, write_json

MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
REGISTRY_USER_AGENT = "AIGC-Harness/0.2 PackRegistry/1"


@dataclass(frozen=True)
class RegistryConfig:
    name: str
    index_url: str
    trusted_keys: dict[str, str]

    def validate(self) -> None:
        _validate_remote_url(self.index_url)
        if not self.name.strip():
            raise PackError("Registry name cannot be empty")
        if not self.trusted_keys:
            raise PackError("Registry requires at least one pinned Ed25519 public key")
        for key_id, encoded in self.trusted_keys.items():
            if not key_id.strip():
                raise PackError("Registry key id cannot be empty")
            _decode_exact_base64(encoded, 32, f"public key {key_id}")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "index_url": self.index_url, "trusted_keys": self.trusted_keys}


class RegistryConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def list(self) -> list[RegistryConfig]:
        payload = read_json(self.path, {"registries": []})
        values = payload.get("registries", []) if isinstance(payload, dict) else []
        configs: list[RegistryConfig] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            keys = item.get("trusted_keys", {})
            config = RegistryConfig(
                name=str(item.get("name", "")),
                index_url=str(item.get("index_url", "")),
                trusted_keys={str(key): str(value) for key, value in keys.items()} if isinstance(keys, dict) else {},
            )
            config.validate()
            configs.append(config)
        return sorted(configs, key=lambda item: item.name)

    def get(self, name: str | None = None) -> RegistryConfig:
        configs = self.list()
        if name:
            match = next((item for item in configs if item.name == name), None)
            if match:
                return match
            raise PackError(f"Unknown Pack Registry: {name}")
        if len(configs) == 1:
            return configs[0]
        if not configs:
            raise PackError("No Pack Registry configured; use 'pack registry add' first")
        raise PackError("Multiple Pack Registries configured; choose one with --registry")

    def add(self, config: RegistryConfig) -> None:
        config.validate()
        configs = {item.name: item for item in self.list()}
        if config.name in configs:
            raise PackError(f"Pack Registry already exists: {config.name}")
        configs[config.name] = config
        self._write(configs.values())

    def remove(self, name: str) -> None:
        configs = {item.name: item for item in self.list()}
        if name not in configs:
            raise PackError(f"Unknown Pack Registry: {name}")
        configs.pop(name)
        self._write(configs.values())

    def trust_key(self, name: str, key_id: str, public_key: str) -> RegistryConfig:
        configs = {item.name: item for item in self.list()}
        config = configs.get(name)
        if config is None:
            raise PackError(f"Unknown Pack Registry: {name}")
        if key_id in config.trusted_keys:
            raise PackError(f"Registry signing key already exists: {key_id}")
        updated = RegistryConfig(config.name, config.index_url, config.trusted_keys | {key_id: public_key})
        updated.validate()
        configs[name] = updated
        self._write(configs.values())
        return updated

    def _write(self, configs: Any) -> None:
        ordered = sorted(configs, key=lambda item: item.name)
        write_json(self.path, {"schema_version": 1, "registries": [item.to_dict() for item in ordered]})


@dataclass(frozen=True)
class RegistryRelease:
    pack_id: str
    version: str
    archive_url: str
    archive_sha256: str
    signature: str
    key_id: str
    harness_requirement: str = "*"
    dependencies: tuple[PackRequirement, ...] = ()

    @classmethod
    def from_dict(cls, pack_id: str, value: dict[str, Any]) -> RegistryRelease:
        dependencies: list[PackRequirement] = []
        raw_dependencies = value.get("dependencies", [])
        if isinstance(raw_dependencies, list):
            for item in raw_dependencies:
                if isinstance(item, str):
                    item_id, separator, constraint = item.partition("@")
                    dependencies.append(PackRequirement(canonical_pack_id(item_id), constraint if separator else "*"))
                elif isinstance(item, dict) and item.get("id"):
                    dependencies.append(PackRequirement(canonical_pack_id(str(item["id"])), str(item.get("version", "*"))))
        release = cls(
            pack_id=canonical_pack_id(pack_id),
            version=str(value.get("version", "")),
            archive_url=str(value.get("url", "")),
            archive_sha256=str(value.get("sha256", "")).lower(),
            signature=str(value.get("signature", "")),
            key_id=str(value.get("key_id", "")),
            harness_requirement=str(value.get("harness", "*")),
            dependencies=tuple(dependencies),
        )
        release.validate()
        return release

    def validate(self) -> None:
        from .pack_manager import parse_version

        parse_version(self.version)
        if not self.archive_url:
            raise PackError(f"Registry release {self.pack_id}@{self.version} has no archive URL")
        if len(self.archive_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.archive_sha256):
            raise PackError(f"Registry release {self.pack_id}@{self.version} has invalid SHA-256")
        if not self.signature or not self.key_id:
            raise PackError(f"Registry release {self.pack_id}@{self.version} is unsigned")

    def requirement_dicts(self) -> list[dict[str, str]]:
        return [item.to_dict() for item in self.dependencies]


@dataclass(frozen=True)
class RegistryIndex:
    name: str
    releases: tuple[RegistryRelease, ...]

    @classmethod
    def from_bytes(cls, payload: bytes) -> RegistryIndex:
        try:
            loaded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackError("Pack Registry returned invalid JSON") from exc
        if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
            raise PackError("Unsupported Pack Registry index schema")
        releases: list[RegistryRelease] = []
        packs = loaded.get("packs", [])
        if not isinstance(packs, list):
            raise PackError("Pack Registry 'packs' must be a list")
        for pack in packs:
            if not isinstance(pack, dict) or not pack.get("id") or not isinstance(pack.get("versions"), list):
                raise PackError("Pack Registry contains an invalid Pack entry")
            releases.extend(
                RegistryRelease.from_dict(str(pack["id"]), release)
                for release in pack["versions"]
                if isinstance(release, dict)
            )
        identities = [(release.pack_id, release.version) for release in releases]
        if len(identities) != len(set(identities)):
            raise PackError("Pack Registry contains a duplicate Pack release")
        return cls(str(loaded.get("name", "remote")), tuple(releases))

    def candidates(self, requirement: PackRequirement) -> list[RegistryRelease]:
        candidates = [
            release
            for release in self.releases
            if release.pack_id == canonical_pack_id(requirement.id) and version_satisfies(release.version, requirement.version)
        ]
        return sorted(candidates, key=lambda item: _version_key(item.version), reverse=True)

    def search(self, query: str = "") -> list[dict[str, Any]]:
        query = query.casefold().strip()
        pack_ids = sorted({release.pack_id for release in self.releases if not query or query in release.pack_id.casefold()})
        return [
            {"id": pack_id, "versions": [release.version for release in self.candidates(PackRequirement(pack_id))]}
            for pack_id in pack_ids
        ]


@dataclass(frozen=True)
class RegistryResolution:
    root: str
    releases: tuple[RegistryRelease, ...]
    packages: tuple[PackPackage, ...] = ()

    def provenance(self, registry: RegistryConfig) -> dict[str, dict[str, Any]]:
        return {
            release.pack_id: {
                "registry": registry.name,
                "index_url": registry.index_url,
                "archive_url": urljoin(registry.index_url, release.archive_url),
                "archive_sha256": release.archive_sha256,
                "signature_key_id": release.key_id,
            }
            for release in self.releases
        }


class RegistryResolver:
    def __init__(self, index: RegistryIndex, current_harness_version: str) -> None:
        self.index = index
        self.harness_version = current_harness_version

    def resolve(self, pack_id: str, constraint: str = "*") -> RegistryResolution:
        def search(
            pending: list[tuple[PackRequirement, tuple[str, ...]]],
            selected: dict[str, RegistryRelease],
            constraints: dict[str, tuple[str, ...]],
        ) -> dict[str, RegistryRelease]:
            if not pending:
                return selected
            requirement, chain = pending[0]
            rest = pending[1:]
            pack_id = canonical_pack_id(requirement.id)
            if pack_id in chain:
                raise PackError(f"Circular remote Pack dependency: {' -> '.join((*chain, pack_id))}")
            pack_constraints = (*constraints.get(pack_id, ()), requirement.version)
            next_constraints = constraints | {pack_id: pack_constraints}
            existing = selected.get(pack_id)
            if existing:
                if all(version_satisfies(existing.version, item) for item in pack_constraints):
                    return search(rest, selected, next_constraints)
                raise PackError(
                    f"Conflicting remote requirements for {pack_id}: selected {existing.version}, "
                    f"constraints are {', '.join(pack_constraints)}"
                )
            available = self.index.candidates(PackRequirement(pack_id, "*"))
            candidates = [
                release
                for release in available
                if all(version_satisfies(release.version, item) for item in pack_constraints)
            ]
            if not candidates:
                raise PackError(f"Registry has no compatible release for {pack_id}@{requirement.version}")
            candidates = [item for item in candidates if version_satisfies(self.harness_version, item.harness_requirement)]
            if not candidates:
                raise PackError(f"No {pack_id}@{requirement.version} release supports AIGC Harness {self.harness_version}")

            failures: list[PackError] = []
            for release in candidates:
                dependencies = [(dependency, (*chain, pack_id)) for dependency in release.dependencies]
                try:
                    return search(dependencies + rest, selected | {pack_id: release}, next_constraints)
                except PackError as exc:
                    failures.append(exc)
            raise failures[-1]

        root = canonical_pack_id(pack_id)
        selected = search([(PackRequirement(root, constraint), ())], {}, {})
        order: list[RegistryRelease] = []
        visited: set[str] = set()

        def visit(release: RegistryRelease) -> None:
            if release.pack_id in visited:
                return
            for dependency in release.dependencies:
                visit(selected[dependency.id])
            visited.add(release.pack_id)
            order.append(release)

        visit(selected[root])
        return RegistryResolution(root, tuple(order))


class PackRegistryClient:
    def __init__(
        self,
        config: RegistryConfig,
        cache_root: Path,
        downloader: Callable[[str, int], bytes] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.cache_root = cache_root
        self._downloader = downloader or _download_bytes

    def fetch_index(self) -> RegistryIndex:
        return RegistryIndex.from_bytes(self._downloader(self.config.index_url, MAX_INDEX_BYTES))

    def search(self, query: str = "") -> list[dict[str, Any]]:
        return self.fetch_index().search(query)

    def download(self, pack_id: str, constraint: str, current_harness_version: str) -> RegistryResolution:
        resolution = RegistryResolver(self.fetch_index(), current_harness_version).resolve(pack_id, constraint)
        packages = tuple(self._materialize(release) for release in resolution.releases)
        catalog = PackCatalog(packages)
        root_package = catalog.get(resolution.root, constraint)
        local_resolution = PackResolver(catalog, current_harness_version).resolve_package(root_package)
        if [item.manifest.id for item in local_resolution.packages] != [item.pack_id for item in resolution.releases]:
            raise PackError("Signed Pack manifests do not match the Registry dependency graph")
        return RegistryResolution(resolution.root, resolution.releases, packages)

    def _materialize(self, release: RegistryRelease) -> PackPackage:
        archive_url = urljoin(self.config.index_url, release.archive_url)
        _validate_remote_url(archive_url)
        archive_path = self.cache_root / "archives" / f"{release.archive_sha256}.zip"
        if archive_path.is_file() and archive_path.stat().st_size <= MAX_ARCHIVE_BYTES:
            archive = archive_path.read_bytes()
        else:
            archive = self._downloader(archive_url, MAX_ARCHIVE_BYTES)
        actual_digest = sha256(archive).hexdigest()
        if not hmac.compare_digest(actual_digest, release.archive_sha256):
            raise PackError(f"SHA-256 mismatch for {release.pack_id}@{release.version}")
        self._verify_signature(release, archive)
        unpacked = self.cache_root / "unpacked" / actual_digest
        if not archive_path.is_file() or archive_path.stat().st_size != len(archive):
            _atomic_write_bytes(archive_path, archive)
        if unpacked.exists():
            shutil.rmtree(unpacked)
        _extract_pack_zip(archive, unpacked)
        package_root = _find_package_root(unpacked)
        manifest = PackManifest.from_file(package_root / "pack.yaml")
        if manifest.id != release.pack_id or manifest.version != release.version:
            raise PackError(
                f"Signed archive identity mismatch: expected {release.pack_id}@{release.version}, got {manifest.id}@{manifest.version}"
            )
        actual_dependencies = tuple((item.id, item.version) for item in manifest.pack_requirements)
        indexed_dependencies = tuple((item.id, item.version) for item in release.dependencies)
        if actual_dependencies != indexed_dependencies or manifest.harness_requirement != release.harness_requirement:
            raise PackError(f"Signed manifest metadata mismatch for {release.pack_id}@{release.version}")
        return PackPackage(manifest, package_root)

    def _verify_signature(self, release: RegistryRelease, archive: bytes) -> None:
        encoded_key = self.config.trusted_keys.get(release.key_id)
        if encoded_key is None:
            raise PackError(f"Registry release uses untrusted signing key: {release.key_id}")
        public_key = _decode_exact_base64(encoded_key, 32, f"public key {release.key_id}")
        signature = _decode_exact_base64(release.signature, 64, "signature")
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, archive)
        except InvalidSignature as exc:
            raise PackError(f"Invalid signature for {release.pack_id}@{release.version}") from exc


def _download_bytes(url: str, maximum: int) -> bytes:
    _validate_remote_url(url)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json, application/zip", "User-Agent": REGISTRY_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
            _validate_remote_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length and int(length) > maximum:
                raise PackError(f"Remote response exceeds {maximum} bytes")
            payload = response.read(maximum + 1)
    except PackError:
        raise
    except Exception as exc:
        raise PackError(f"Unable to download Pack Registry resource: {type(exc).__name__}: {exc}") from exc
    if len(payload) > maximum:
        raise PackError(f"Remote response exceeds {maximum} bytes")
    return payload


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise PackError("Pack Registry URLs may not contain credentials")
    is_local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_local_http:
        raise PackError("Pack Registry URLs must use HTTPS; HTTP is allowed only for localhost")
    if not parsed.hostname:
        raise PackError("Pack Registry URL has no host")


def _decode_exact_base64(value: str, expected_length: int, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PackError(f"Invalid base64 {label}") from exc
    if len(decoded) != expected_length:
        raise PackError(f"Invalid {label} length: expected {expected_length} bytes")
    return decoded


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _extract_pack_zip(payload: bytes, destination: Path) -> None:
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_FILES:
                raise PackError(f"Pack archive contains more than {MAX_ARCHIVE_FILES} entries")
            total_size = sum(member.file_size for member in members)
            if total_size > MAX_EXTRACTED_BYTES:
                raise PackError(f"Pack archive expands beyond {MAX_EXTRACTED_BYTES} bytes")
            staging.mkdir(parents=True, exist_ok=False)
            extracted_size = 0
            normalized_paths: set[str] = set()
            for member in members:
                relative = PurePosixPath(member.filename)
                if (
                    not member.filename
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or "\x00" in member.filename
                    or "\\" in member.filename
                    or any(":" in part for part in relative.parts)
                ):
                    raise PackError(f"Unsafe path in Pack archive: {member.filename!r}")
                normalized = unicodedata.normalize("NFC", relative.as_posix().rstrip("/")).casefold()
                if normalized in normalized_paths:
                    raise PackError(f"Duplicate path in Pack archive: {member.filename}")
                normalized_paths.add(normalized)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PackError(f"Symbolic links are not allowed in Pack archives: {member.filename}")
                file_type = stat.S_IFMT(mode)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise PackError(f"Special files are not allowed in Pack archives: {member.filename}")
                if member.flag_bits & 0x1:
                    raise PackError("Encrypted Pack archives are not supported")
                target = staging.joinpath(*relative.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        extracted_size += len(chunk)
                        if extracted_size > MAX_EXTRACTED_BYTES:
                            raise PackError(f"Pack archive expands beyond {MAX_EXTRACTED_BYTES} bytes")
                        output.write(chunk)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, destination)
    except zipfile.BadZipFile as exc:
        raise PackError("Downloaded Pack is not a valid ZIP archive") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _find_package_root(unpacked: Path) -> Path:
    manifests = list(unpacked.rglob("pack.yaml"))
    if len(manifests) != 1:
        raise PackError(f"Pack archive must contain exactly one pack.yaml; found {len(manifests)}")
    return manifests[0].parent


def _version_key(value: str) -> tuple[int, int, int]:
    from .pack_manager import parse_version

    return parse_version(value)
