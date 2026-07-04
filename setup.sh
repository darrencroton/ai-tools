#!/usr/bin/env bash
# ~/Documents/AI Tools/setup.sh  (lives in the synced shared config directory)
#
# Wires up shared AI agent configuration for the tools registered in tools.conf.
# Safe to re-run — skips anything already configured, warns on conflicts.
#
# ── What this sets up ────────────────────────────────────────────────────────
#
#   ~/Documents/AI Tools/   ← single source of truth (this directory)
#   ├── AGENTS.md                    global instructions for all AI tools
#   ├── setup.sh                     this script
#   ├── tools.conf                   registered AI tools (edit to add new ones)
#   └── skills/                      shared skill library
#
#   ~/.agents/              → ~/Documents/AI Tools/     (symlink, created manually)
#   <tool>/<instructions>   → ~/.agents/AGENTS.md       (per tools.conf)
#   <tool>/skills           → ~/.agents/skills/          (per tools.conf)
#
# ── New machine setup (two commands) ─────────────────────────────────────────
#
#   Prerequisites: AI tools installed and signed in,
#                  the shared config directory synced or copied into place.
#
#   ln -s "$HOME/Documents/AI Tools" ~/.agents
#   bash ~/.agents/setup.sh
#
# ── Adding a new AI tool ─────────────────────────────────────────────────────
#
#   1. Add a line to ~/Documents/AI Tools/tools.conf:
#        <config_dir>    <instructions_filename>
#   2. Re-run: bash ~/.agents/setup.sh
#   No other changes needed.
#
# ── Adding new skills ─────────────────────────────────────────────────────────
#
#   Drop a new skill directory into ~/Documents/AI Tools/skills/.
#   No re-run needed — the whole skills dir is symlinked, so it appears
#   in all tools instantly.
#
# ── If setup.sh warns about an existing file ─────────────────────────────────
#
#   A WARNING means a regular file already exists where a symlink is needed.
#   The script never overwrites — resolve manually:
#
#   1. Back up the existing file (review it, merge anything useful into
#      ~/.agents/AGENTS.md, then discard):
#        cp "<tool>/<instructions_filename>" ~/agent-instructions-backup.md
#
#   2. Force-create the symlink:
#        ln -sf "$HOME/.agents/AGENTS.md" "<tool>/<instructions_filename>"
#
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

AGENTS_DIR="$HOME/.agents"
TOOLS_CONF="$AGENTS_DIR/tools.conf"

echo "=== Shared agent setup ==="
echo "    Source: $(realpath "$AGENTS_DIR")"
echo ""

# ── Preflight ────────────────────────────────────────────────────────────────

if [[ ! -L "$AGENTS_DIR" ]]; then
    echo "ERROR: ~/.agents is not a symlink."
    echo "Create it first, then re-run:"
    echo "  ln -s \"\$HOME/Documents/AI Tools\" ~/.agents"
    exit 1
fi

if [[ ! -f "$TOOLS_CONF" ]]; then
    echo "ERROR: $TOOLS_CONF not found."
    exit 1
fi

# ── Helper ───────────────────────────────────────────────────────────────────

_link() {
    local src="$1" dst="$2"
    if [[ -L "$dst" ]]; then
        echo "      $dst already symlinked — skipping"
    elif [[ -d "$dst" ]]; then
        echo "      WARNING: $dst exists as a real directory — not replacing"
        echo "      Move it out of the way first, then re-run:"
        echo "        mv \"$dst\" ~/$(basename "$dst")-old"
    elif [[ -e "$dst" ]]; then
        echo "      WARNING: $dst exists as a regular file — not replacing"
        echo "      Review manually, then: ln -sf \"$src\" \"$dst\""
    else
        ln -s "$src" "$dst"
        echo "      $dst -> $src"
    fi
}

# ── Per-tool instruction and skills symlinks (driven by tools.conf) ────────

echo "Tool symlinks (from tools.conf)..."

while IFS= read -r line; do
    # Skip blank lines and comments
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue

    config_dir="$(echo "$line" | awk '{print $1}')"
    instructions_file="$(echo "$line" | awk '{print $2}')"

    # Expand ~ in config_dir
    config_dir="${config_dir/#\~/$HOME}"

    if [[ ! -d "$config_dir" ]]; then
        echo ""
        echo "    [$config_dir / $instructions_file] — config dir not found, skipping"
        continue
    fi

    echo ""
    echo "    [$config_dir / $instructions_file]"

    # Instruction file symlink
    _link "$AGENTS_DIR/AGENTS.md" "$config_dir/$instructions_file"

    # Skills directory symlink (whole dir — adding/removing skills in iCloud
    # is reflected instantly in all tools, no re-run needed)
    _link "$AGENTS_DIR/skills" "$config_dir/skills"

done < "$TOOLS_CONF"
