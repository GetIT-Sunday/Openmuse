# Host a Pack Registry on GitHub Pages

GitHub Pages is the first official hosting Adapter for the AIGC Harness Pack
Registry. The Registry remains ordinary static files, so Pack identity,
signatures, dependency resolution, and consumer lockfiles do not depend on
GitHub.

## Prepare the site

First build, sign, and publish releases into `public-registry/` as described in
[`PACK_PUBLISHING.md`](PACK_PUBLISHING.md). Then run:

```bash
python -m smearglepaper pack deploy github-pages \
  --registry-dir public-registry \
  --output-dir pages \
  --workflow-out .github/workflows/deploy-pack-registry.yml
```

The Adapter validates `index.json`, checks every referenced archive SHA-256,
opens each archive with the same safety rules used by the publisher, and
confirms that its manifest matches the indexed release. It copies only indexed
archives and adds `.nojekyll`.

The output directory is managed by the Adapter. It can be prepared repeatedly,
but the command refuses to replace a non-empty directory that lacks its AIGC
Harness ownership marker.

## Deploy from GitHub

1. Commit `pages/` and `.github/workflows/deploy-pack-registry.yml` to the
   Registry repository.
2. In the repository's Pages settings, choose **GitHub Actions** as the source.
3. Run **Deploy Pack Registry to GitHub Pages** from the Actions tab.

The generated workflow is manual-only and uses these least-privilege
permissions:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

It does not build or sign Packs in GitHub. Private signing keys therefore do
not need to be stored as repository secrets.

The workflow follows GitHub's documented Pages pipeline using
`configure-pages`, `upload-pages-artifact`, and `deploy-pages`. See
[Using custom workflows with GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

For a project site, the Registry URL is normally:

```text
https://<owner>.github.io/<repository>/index.json
```

For an organization or user site named `<owner>.github.io`, it is:

```text
https://<owner>.github.io/index.json
```

## Configure a consumer

Distribute the Ed25519 public key and key ID through a channel users can verify.
The Registry index deliberately does not make its own signing key trusted.

```bash
python -m smearglepaper pack registry add official \
  https://<owner>.github.io/<repository>/index.json \
  --key-id autowechat-2026 \
  --public-key '<base64-public-key>'

python -m smearglepaper pack search --registry official
python -m smearglepaper pack install autowechat/research-to-wechat \
  --registry official
```

Preparing the site has no network side effects. Committing, pushing, enabling
Pages, and running the workflow remain explicit repository-owner actions.
