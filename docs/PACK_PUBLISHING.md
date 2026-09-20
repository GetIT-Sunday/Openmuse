# Publishing an AIGC Capability Pack

The publishing pipeline produces a signed static Registry in four explicit
steps. None of these commands uploads files or contacts an external service.

## 1. Generate an author signing key

Do this once per publisher or planned key rotation:

```bash
python -m smearglepaper pack keygen \
  --to ~/.aigc/keys/autowechat-2026.pem \
  --key-id autowechat-2026
```

The private key is created with mode `0600`. Keep it outside the repository,
back it up securely, and never upload it. Publish the returned base64 public
key through a verified website or repository so consumers can pin it.

## 2. Build a self-contained Pack

```bash
python -m smearglepaper pack build \
  autowechat/research-to-wechat \
  --to dist
```

The builder validates the Pack, materializes referenced local Skills, sorts
all entries, normalizes ZIP timestamps and permissions, and produces a stable
archive name and SHA-256. Rebuilding identical content produces identical
bytes.

Build refuses symbolic links, `.env` files, private-key payloads, oversized
content, and archives with too many files. Cache files, `.git`, `.DS_Store`,
and Python bytecode are excluded.

## 3. Sign the exact archive

```bash
python -m smearglepaper pack sign \
  dist/autowechat-research-to-wechat-0.1.0.zip \
  --private-key ~/.aigc/keys/autowechat-2026.pem \
  --key-id autowechat-2026
```

This creates a neighboring `.release.json` descriptor containing the Pack
identity, dependency metadata, archive digest, Ed25519 signature, key ID, and
public key. The private key is never copied into the descriptor.

## 4. Publish into a static Registry directory

```bash
python -m smearglepaper pack publish \
  dist/autowechat-research-to-wechat-0.1.0.release.json \
  --registry-dir public-registry \
  --name "AutoWechat Pack Registry"
```

The publisher independently verifies the digest, signature, archive safety,
and signed manifest metadata. It then produces or updates:

```text
public-registry/
  index.json
  packs/
    autowechat/
      research-to-wechat/
        0.1.0.zip
```

Publishing the same identical release is idempotent. Reusing an existing
Pack version with different bytes or metadata is rejected; release a new
version instead.

## Deploy the directory

Deploy only the contents of `public-registry/` to an HTTPS static host. Common
options include GitHub Pages, Cloudflare R2, AWS S3, Alibaba Cloud OSS, and
Tencent COS. Upload automation belongs in a provider Adapter; it is separate
from Pack building and signing so local publication never causes an implicit
network side effect.

GitHub Pages is the first supported deployment target. Prepare a validated
Pages directory and, optionally, a manually triggered workflow with:

```bash
python -m smearglepaper pack deploy github-pages \
  --registry-dir public-registry \
  --output-dir pages \
  --workflow-out .github/workflows/deploy-pack-registry.yml
```

See [`GITHUB_PAGES_REGISTRY.md`](GITHUB_PAGES_REGISTRY.md) for repository setup
and consumer configuration.

After deployment, consumers pin the public key and Registry URL as described
in [`PACK_REGISTRY_PROTOCOL.md`](PACK_REGISTRY_PROTOCOL.md).
