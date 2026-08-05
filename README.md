# AI Agent Home

Private bootstrap and configuration home for coding-agent harnesses.

This repo owns my global `AGENTS.md`, setup script, manifest, and private/local skills. It composes skills from external repos into one canonical skill catalogue at `~/.agents/skills` and links Claude Code, Codex, OpenCode, Copilot, Qwen Code, and related tools to the same shared agent environment.

## Layout

```text
~/.agents/                 canonical agent home (usually a symlink to this repo's clone)
  AGENTS.md                global instructions shared by every harness
  setup.sh                 idempotent bootstrap/maintenance script
  manifest.yml             declarative config: repos to clone, tools to link
  home-skills/             physical private/local skills owned by this repo
  home-config/             physical per-tool settings/hook files (gitignored)
    claude/settings.json, claude/hooks/pm-poll-guard.py, claude/statusline-command.sh
    copilot/settings.json
    qwen/settings.json
    opencode/opencode.json
  repos/                   external skill repo clones (gitignored, managed by setup)
    ai-agent-coder/
  skills/                  composed public skill catalogue (gitignored, generated)
    <private-skill>  -> ../home-skills/<private-skill>
    <public-skill>   -> ../repos/ai-agent-coder/skills/<public-skill>
```

`~/.agents/skills` is the only public skill surface. Every harness points at it — never directly at `home-skills/` or a repo's `skills/` directory:

```text
~/.claude/CLAUDE.md                -> ~/.agents/AGENTS.md
~/.claude/skills                   -> ~/.agents/skills
~/.codex/AGENTS.md                 -> ~/.agents/AGENTS.md
~/.codex/skills                    -> ~/.agents/skills
~/.config/opencode/AGENTS.md       -> ~/.agents/AGENTS.md
~/.config/opencode/skills          -> ~/.agents/skills
~/.copilot/copilot-instructions.md -> ~/.agents/AGENTS.md
~/.copilot/skills                  -> ~/.agents/skills
~/.qwen/QWEN.md                    -> ~/.agents/AGENTS.md
~/.qwen/skills                     -> ~/.agents/skills
```

A second, narrower mechanism (`config_links` in the manifest) centralizes individual per-tool settings/hook files the same way:

```text
~/.claude/settings.json             -> ~/.agents/home-config/claude/settings.json
~/.claude/hooks/pm-poll-guard.py    -> ~/.agents/home-config/claude/hooks/pm-poll-guard.py
~/.claude/statusline-command.sh     -> ~/.agents/home-config/claude/statusline-command.sh
~/.copilot/settings.json            -> ~/.agents/home-config/copilot/settings.json
~/.qwen/settings.json               -> ~/.agents/home-config/qwen/settings.json
~/.config/opencode/opencode.json    -> ~/.agents/home-config/opencode/opencode.json
```

Only use `config_links` for files that are pure preference/config. Files a tool treats as machine-local mutable state (e.g. Codex's `config.toml`, which mixes real settings with an auto-growing per-machine trusted-projects list and a local notify-app path) should stay native rather than being centralized. `home-config/` itself is gitignored — these files get rewritten by the tools at runtime (timestamps, cached state), so they sync across Macs via iCloud on the physical repo directory, not via git commits.

The public reusable skills live in [`ai-agent-coder`](https://github.com/darrencroton/ai-agent-coder); this repo clones and composes them via the manifest. This repo itself is the base install — it is not listed as a repo dependency in its own manifest.

## Install

1. Clone this repo somewhere permanent. An iCloud-synced directory works — `~/.agents` is then a symlink into it, which is how the same setup mirrors across two Macs.
2. Run the setup script:

```bash
bash <clone-path>/setup.sh
```

It creates the `~/.agents` symlink if absent, creates `repos/`, `home-skills/`, and `skills/`, clones the manifest repos, composes the skill catalogue, and links every installed tool. Safe to re-run at any time.

## Setup script

```bash
./setup.sh                # normal run
./setup.sh --dry-run      # print planned actions without changing anything
./setup.sh --force        # replace conflicting files/symlinks (never deletes real directories)
./setup.sh --backup       # move conflicting files/directories to timestamped backups
./setup.sh --no-update    # skip `git pull` in existing repos
./setup.sh --check        # verify everything is in place; exit non-zero on problems
```

Conflict policy: a correct symlink is left alone; a wrong symlink or real file is reported and only replaced with `--force` (or moved aside with `--backup`); a real directory is only ever moved aside with `--backup`. Setup never silently deletes data, and when rebuilding `skills/` it only touches symlinks that point into `home-skills/` or `repos/` — real directories and foreign entries (such as harness-installed system skills) are preserved.

Duplicate skill names across sources abort the run before any catalogue change; disable a source repo in the manifest or rename a skill directory to resolve.

## Manifest

`manifest.yml` declares the agent home path, the external skill repos to clone under `repos/` (with per-repo `branch`, `enabled`, `update`), the tool config directories to link, and individual settings/hook files to centralize under `home-config/`. See the comment header in [`manifest.yml`](manifest.yml) for the exact (deliberately constrained) format. Adding a new harness, skill repo, or centralized settings file is one manifest entry plus a re-run of `setup.sh`.

## Adding skills

- **Private/local skill**: create `home-skills/<name>/SKILL.md`, re-run `./setup.sh`.
- **Public skill**: add it to `ai-agent-coder` (or another manifest repo), push, re-run `./setup.sh`.

## Validation

After setup, these should all resolve:

```bash
./setup.sh --check

ls -l ~/.agents ~/.agents/repos ~/.agents/skills
cat ~/.agents/AGENTS.md
cat ~/.agents/skills/master-controller/SKILL.md

readlink ~/.claude/skills
readlink ~/.codex/AGENTS.md
readlink ~/.config/opencode/skills
readlink ~/.copilot/copilot-instructions.md

readlink ~/.claude/settings.json
readlink ~/.claude/hooks/pm-poll-guard.py
readlink ~/.claude/statusline-command.sh
readlink ~/.copilot/settings.json
readlink ~/.qwen/settings.json
readlink ~/.config/opencode/opencode.json
```

## Files

- `AGENTS.md`: global instructions used across AI coding assistants
- `setup.sh`: bootstrap/maintenance script (see above)
- `manifest.yml`: repos and tool links, read by `setup.sh`
- `home-skills/`: private skills; each documents itself in its own `SKILL.md`
- Generated directories and local artefacts are excluded via `.gitignore`
