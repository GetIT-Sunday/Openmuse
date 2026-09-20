# AIGC Harness Skills and Packs

AutoWechat is the first content pack for the AIGC Harness. There are two useful
units at different layers:

- A **Skill** is the smallest model-visible capability and the best unit for
  copying into Codex or Claude Code.
- A **Pack** is the installable distribution unit. It owns a versioned,
  self-contained set of Skills, workflows, templates, configuration schemas,
  and optional adapters.

The canonical Pack identity is `publisher/name@version`, for example
`autowechat/research-to-wechat@0.1.0`. A Pack never becomes another runtime:
after it is enabled, the Harness discovers and exposes its Skills.

## Portable contract

Each Skill is a directory containing `SKILL.md` and optional `references/`,
`scripts/`, and `assets/`. The first YAML block in `SKILL.md` is the manifest:

```yaml
id: autowechat.paper-deep-read
name: paper-deep-read
version: 0.1.0
description: Deep-read a research paper and produce evidence-grounded notes.
requires: []
```

The Markdown instructions and resource folders are portable. Harness-specific
tool registration, memory, approval, events, and UI behavior stay in adapters.

The SDK contracts live in `smearglepaper.skill_adapter`:

- `ModelGateway` supplies streaming model text.
- `MemoryBackend` supplies recall, remember, and clear.
- `ApprovalGate` handles side-effect confirmation.
- `ArtifactBackend` stores generated files.
- `SkillAdapter` turns a `SkillRequest` into a `SkillResult`.

An adapter can implement these protocols in Codex, Claude Code, or another
Harness without importing the AutoWechat Runtime or TUI.

## Local commands

```bash
python -m smearglepaper skill list
python -m smearglepaper skill show autowechat.write-paper-wechat
python -m smearglepaper skill validate
python -m smearglepaper pack list
python -m smearglepaper pack show autowechat/research-to-wechat
python -m smearglepaper pack resolve autowechat/research-to-wechat
python -m smearglepaper pack install packs/research-to-wechat
python -m smearglepaper pack status
python -m smearglepaper pack disable autowechat/research-to-wechat
python -m smearglepaper pack enable autowechat/research-to-wechat
python -m smearglepaper pack build autowechat/research-to-wechat --to dist
```

`skill show` exposes the same manifest fields in machine-readable JSON, making
it suitable for an installer or registry client.

## Export to another Harness

Codex and OpenCode both discover directory-based Skills from `.agents/skills`.
OpenCode also supports `.opencode/skills` and `.claude/skills`. Export to the
shared project or user directory when the same Skill should work in both:

```bash
python -m smearglepaper skill export paper-deep-read --to ./.agents/skills
python -m smearglepaper skill export paper-deep-read --to ~/.agents/skills
python -m smearglepaper pack export autowechat/research-to-wechat --to ./portable-packs
```

Pack export creates a self-contained Pack directory and materializes its Skill
files inside `skills/`. The result can be zipped, checked into another
repository, or installed without bringing along the AIGC Harness runtime.

This portability applies directly to each Skill directory. Codex recommends
Plugins when distributing one or more Skills beyond local or repository use;
OpenCode's Skills documentation describes directory discovery rather than a
signed package Registry. The AIGC Pack layer adds semantic versions,
dependencies, signatures, lockfiles, and Registry installation without
changing the portable `SKILL.md` unit.

References:

- [Codex: Build skills](https://learn.chatgpt.com/docs/build-skills)
- [OpenCode: Agent Skills](https://opencode.ai/docs/skills/)

## Pack manifest and dependency resolution

`pack.yaml` declares the Harness compatibility range and Pack dependencies:

```yaml
id: autowechat/research-to-wechat
name: Research to WeChat
version: 0.1.0
requires:
  harness: ">=0.2.0"
  packs:
    - id: aigc/research
      version: "^1.0.0"
skills:
  - source: skills/paper-deep-read
workflows:
  - workflows/paper-to-wechat.yaml
```

Path-based Workflow entries must exist inside the Pack and are included in
build artifacts. A target Harness may execute the declaration directly or map
it through an Adapter; it must not silently substitute an unrelated workflow.

Version constraints intentionally start small and deterministic: `*`, exact
versions, `>=`, and compatible `^` ranges are supported. Resolution is
recursive, rejects missing or cyclic dependencies, deduplicates shared
dependencies, and produces dependency-first installation order.

The installer supports local directories and signed remote Registries. It
validates before copying, never runs install scripts, rejects symbolic links
and escaping Skill paths, and stores project state in:

```text
.aigc/
  packs/<publisher>/<name>/
  pack-lock.json
  enabled-packs.json
```

Installed Packs keep their own Skills instead of scattering files into one
shared folder. That makes ownership, disable, uninstall, and whole-Pack
migration predictable. The remote index, pinned-key trust model, signing
payload, download limits, and archive rules are specified in
[`PACK_REGISTRY_PROTOCOL.md`](PACK_REGISTRY_PROTOCOL.md).

## Compatibility boundary

Prompt instructions, references, scripts, and input/output contracts are
portable. Model APIs, tool schemas, approval UI, event streams, memory stores,
and publishing adapters require a target Harness adapter.
