# AIGC Pack Registry Protocol v1

An AIGC Pack Registry is a static HTTPS JSON index plus signed ZIP archives.
It does not need a custom server: object storage, a CDN, or GitHub Pages can
host the files.

Pack authors can generate this directory with the workflow documented in
[`PACK_PUBLISHING.md`](PACK_PUBLISHING.md).

## Trust model

The Registry signing key is pinned locally before the first installation. The
public key must come from a trusted channel such as the publisher's verified
website or repository. A key returned inside the Registry index is never
trusted automatically.

Each release contains:

- SHA-256 of the exact ZIP bytes, for integrity and cache identity.
- Ed25519 signature of the exact ZIP bytes, for publisher authenticity.
- Signing key ID, allowing controlled key rotation.

The client also compares the signed `pack.yaml` identity, Harness requirement,
and dependency list with the index before installation.

## Index format

```json
{
  "schema_version": 1,
  "name": "AIGC Community",
  "packs": [
    {
      "id": "autowechat/research-to-wechat",
      "versions": [
        {
          "version": "0.1.0",
          "url": "packs/research-to-wechat-0.1.0.zip",
          "sha256": "<64 lowercase hex characters>",
          "signature": "<base64 Ed25519 signature over ZIP bytes>",
          "key_id": "autowechat-2026",
          "harness": ">=0.2.0",
          "dependencies": []
        }
      ]
    }
  ]
}
```

Archive URLs may be absolute HTTPS URLs or paths relative to the index. URLs
containing credentials are rejected. HTTP is accepted only for localhost
during Registry development.

## Consumer workflow

Pin the Registry and its raw 32-byte Ed25519 public key, encoded as base64:

```bash
python -m smearglepaper pack registry add official \
  https://packs.example.com/index.json \
  --key-id autowechat-2026 \
  --public-key '<base64-public-key>'
```

Then search, inspect the dependency plan, and install:

```bash
python -m smearglepaper pack search research --registry official
python -m smearglepaper pack resolve autowechat/research-to-wechat --registry official
python -m smearglepaper pack install autowechat/research-to-wechat --registry official
```

During key rotation, pin the new key before releases start using it:

```bash
python -m smearglepaper pack registry trust-key official \
  --key-id autowechat-2027 \
  --public-key '<new-base64-public-key>'
```

When exactly one Registry is configured, `--registry` is optional for search
and install. `--version` accepts the same `*`, exact, `>=`, and `^` constraints
as local Pack resolution.

## Archive safety limits

The v1 client accepts ZIP only and enforces:

- 2 MiB maximum index response.
- 50 MiB maximum compressed archive.
- 100 MiB maximum extracted content.
- 1,000 entries maximum.
- Exactly one `pack.yaml`.
- No absolute paths, parent traversal, backslashes, Windows drive/ADS syntax,
  symbolic links, special files, encrypted entries, or case-folding collisions.

Verified archives are cached by SHA-256 under `.aigc/cache/`. Installation
still re-verifies the digest and signature and re-extracts from the trusted
archive, so modified unpacked cache content is never installed.
