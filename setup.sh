#!/usr/bin/env bash
# setup.sh — bootstrap and maintain the shared AI agent home.
#
# Reads manifest.yml (next to this script) and:
#
#   1. ensures the canonical agent home (default ~/.agents) points at this
#      repo — it may be a real directory or, more usually, a symlink to a
#      synced clone; both work;
#   2. ensures the working directories exist:
#        <agent_home>/repos/        external skill repo clones
#        <agent_home>/home-skills/  physical private/local skills (this repo)
#        <agent_home>/skills/       composed public skill catalogue
#   3. clones missing enabled repos from the manifest into repos/ and
#      updates existing ones with `git pull --ff-only` (unless disabled);
#   4. rebuilds the composed catalogue <agent_home>/skills/ as symlinks:
#        <skill> -> ../home-skills/<skill>            (local skills)
#        <skill> -> ../repos/<repo>/skills/<skill>    (external skills)
#      Only symlinks pointing into home-skills/ or repos/ are ever touched.
#      Real directories/files and foreign symlinks (e.g. harness-installed
#      system skills) are always preserved and reported on conflict.
#   5. links each tool registered under tool_links to the shared home:
#        <config_dir>/<instructions_link> -> <agent_home>/AGENTS.md
#        <config_dir>/<skills_link>       -> <agent_home>/skills
#
# The script is idempotent — safe to re-run at any time.
#
# ── Usage ────────────────────────────────────────────────────────────────────
#
#   ./setup.sh                normal run
#   ./setup.sh --dry-run      print planned actions without changing anything
#   ./setup.sh --force        replace conflicting files/symlinks (never
#                             deletes real directories — use --backup)
#   ./setup.sh --backup       move conflicting files/directories to
#                             timestamped backups, then link
#   ./setup.sh --no-update    skip `git pull` in existing repos
#   ./setup.sh --check        verify everything is in place; change nothing;
#                             exit non-zero if anything is missing or wrong
#
# ── New machine setup ────────────────────────────────────────────────────────
#
#   1. Clone (or let iCloud sync) this repo somewhere permanent.
#   2. Run:  bash <clone-path>/setup.sh
#      (creates the ~/.agents symlink automatically if absent)
#
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$SCRIPT_DIR/manifest.yml"

# ── Flags ────────────────────────────────────────────────────────────────────

DRY_RUN=0 FORCE=0 BACKUP=0 NO_UPDATE=0 CHECK=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=1 ;;
        --force)     FORCE=1 ;;
        --backup)    BACKUP=1 ;;
        --no-update) NO_UPDATE=1 ;;
        --check)     CHECK=1 ;;
        -h|--help)   sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "ERROR: unknown flag: $arg (try --help)" >&2; exit 2 ;;
    esac
done

if [[ $FORCE -eq 1 && $BACKUP -eq 1 ]]; then
    echo "ERROR: --force and --backup are mutually exclusive." >&2
    exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
FAILURES=0
CHANGES=0

# ── Output helpers ───────────────────────────────────────────────────────────

say()  { echo "$*"; }
note() { echo "      $*"; }
fail() { echo "      FAIL: $*"; FAILURES=$((FAILURES + 1)); }

# Execute a mutating command, or print it in dry-run mode.
act() {
    CHANGES=$((CHANGES + 1))
    if [[ $DRY_RUN -eq 1 ]]; then
        note "[dry-run] $*"
    else
        "$@"
    fi
}

expand_tilde() {
    case "$1" in
        "~") echo "$HOME" ;;
        "~/"*) echo "$HOME/${1#\~/}" ;;
        *) echo "$1" ;;
    esac
}

# ── Manifest parsing ─────────────────────────────────────────────────────────
# Parses the constrained YAML subset documented in manifest.yml into
# tab-separated records:
#   scalar <TAB> key=value
#   <section> <TAB> key=value <TAB> key=value ...
parse_manifest() {
    awk '
        function trim(s) { gsub(/^[ \t]+|[ \t\r]+$/, "", s); return s }
        function strip_comment(s) { sub(/[ \t]#.*$/, "", s); return s }
        function split_kv(line,   k, v) {
            k = line; sub(/:.*/, "", k); k = trim(k)
            v = line; sub(/^[^:]*:/, "", v); v = trim(v)
            return k "=" v
        }
        function emit() { if (rec != "") print section "\t" rec; rec = "" }
        /^[ \t]*#/ { next }
        /^[ \t]*$/ { next }
        /^[^ \t-]/ {                       # top-level key
            emit()
            line = strip_comment($0)
            k = line; sub(/:.*/, "", k); k = trim(k)
            v = line; sub(/^[^:]*:/, "", v); v = trim(v)
            if (v != "") { print "scalar\t" k "=" v; section = "" }
            else section = k
            next
        }
        /^[ \t]*-[ \t]/ {                  # list item start
            emit()
            line = strip_comment($0); sub(/^[ \t]*-[ \t]*/, "", line)
            rec = split_kv(line)
            next
        }
        {                                  # list item continuation
            line = strip_comment($0)
            if (trim(line) == "") next
            rec = rec "\t" split_kv(line)
        }
        END { emit() }
    ' "$MANIFEST"
}

# Extract one field from a tab-separated key=value record.
field() {
    local record="$1" key="$2" default="${3:-}" kv
    while IFS= read -r kv; do
        if [[ "$kv" == "$key="* ]]; then
            echo "${kv#"$key"=}"
            return
        fi
    done < <(printf '%s\n' "$record" | tr '\t' '\n')
    echo "$default"
}

[[ -f "$MANIFEST" ]] || { echo "ERROR: manifest not found: $MANIFEST" >&2; exit 1; }

AGENT_HOME_RAW="~/.agents"
REPO_RECORDS=()
TOOL_RECORDS=()

while IFS= read -r line; do
    section="${line%%$'\t'*}"
    record="${line#*$'\t'}"
    case "$section" in
        scalar)
            [[ "$record" == agent_home=* ]] && AGENT_HOME_RAW="${record#agent_home=}" ;;
        repos)      REPO_RECORDS+=("$record") ;;
        tool_links) TOOL_RECORDS+=("$record") ;;
    esac
done < <(parse_manifest)

AGENT_HOME="$(expand_tilde "$AGENT_HOME_RAW")"

MODE="setup"
[[ $CHECK -eq 1 ]] && MODE="check"
say "=== Agent home $MODE ==="
say "    Manifest:   $MANIFEST"
say "    Agent home: $AGENT_HOME"
say ""

# ── 1. Agent home ────────────────────────────────────────────────────────────
# ~/.agents may be a real directory or a symlink (e.g. to an iCloud-synced
# clone). It must resolve to the directory containing this script.

say "Agent home..."

if [[ -e "$AGENT_HOME" || -L "$AGENT_HOME" ]]; then
    RESOLVED="$(realpath "$AGENT_HOME" 2>/dev/null || true)"
    if [[ "$RESOLVED" == "$SCRIPT_DIR" ]]; then
        note "$AGENT_HOME -> $RESOLVED (ok)"
    else
        fail "$AGENT_HOME resolves to '$RESOLVED', expected '$SCRIPT_DIR'."
        note "Fix manually, e.g.: ln -sfn \"$SCRIPT_DIR\" \"$AGENT_HOME\""
    fi
elif [[ $CHECK -eq 1 ]]; then
    fail "$AGENT_HOME does not exist."
else
    act ln -s "$SCRIPT_DIR" "$AGENT_HOME"
    note "$AGENT_HOME -> $SCRIPT_DIR (created)"
fi

# All file operations below go through the resolved physical path; all
# symlink *targets* use the canonical $AGENT_HOME path so that relocating the
# physical clone only requires re-pointing the ~/.agents symlink.
HOME_REAL="$SCRIPT_DIR"

REPOS_DIR="$HOME_REAL/repos"
HOME_SKILLS_DIR="$HOME_REAL/home-skills"
CATALOGUE_DIR="$HOME_REAL/skills"

say ""
say "Directories..."
for d in "$REPOS_DIR" "$HOME_SKILLS_DIR" "$CATALOGUE_DIR"; do
    if [[ -d "$d" ]]; then
        note "$d (ok)"
    elif [[ $CHECK -eq 1 ]]; then
        fail "$d missing"
    else
        act mkdir -p "$d"
        note "$d (created)"
    fi
done

# ── 2. External repos ────────────────────────────────────────────────────────

say ""
say "External repos..."

ENABLED_REPOS=()

for record in ${REPO_RECORDS[@]+"${REPO_RECORDS[@]}"}; do
    name="$(field "$record" name)"
    url="$(field "$record" url)"
    branch="$(field "$record" branch main)"
    enabled="$(field "$record" enabled true)"
    update="$(field "$record" update true)"

    if [[ -z "$name" || -z "$url" ]]; then
        fail "manifest repo entry missing name or url: $record"
        continue
    fi
    if [[ "$enabled" != "true" ]]; then
        note "[$name] disabled — skipping"
        continue
    fi
    ENABLED_REPOS+=("$name")

    repo_dir="$REPOS_DIR/$name"
    if [[ ! -d "$repo_dir" ]]; then
        if [[ $CHECK -eq 1 ]]; then
            fail "[$name] not cloned at $repo_dir"
        else
            note "[$name] cloning $url (branch $branch)..."
            act git clone --branch "$branch" "$url" "$repo_dir"
        fi
        continue
    fi
    if [[ ! -d "$repo_dir/.git" ]]; then
        note "[$name] WARNING: $repo_dir exists but is not a git clone — leaving as-is"
        continue
    fi
    if [[ $CHECK -eq 1 ]]; then
        note "[$name] cloned (ok)"
    elif [[ $NO_UPDATE -eq 1 || "$update" != "true" ]]; then
        note "[$name] update skipped"
    elif [[ $DRY_RUN -eq 1 ]]; then
        note "[dry-run] git -C $repo_dir pull --ff-only"
    elif git -C "$repo_dir" pull --ff-only >/dev/null 2>&1; then
        note "[$name] updated (ff-only)"
    else
        note "[$name] WARNING: git pull --ff-only failed (no upstream, offline, or diverged) — continuing"
    fi
done

# ── 3. Composed skill catalogue ──────────────────────────────────────────────
# Desired state: skills/<name> -> ../home-skills/<name> or
# ../repos/<repo>/skills/<name>, discovered by the presence of SKILL.md.

say ""
say "Skill catalogue ($CATALOGUE_DIR)..."

SKILL_NAMES=()
SKILL_TARGETS=()   # relative symlink target for the matching SKILL_NAMES entry

add_skill() {
    local name="$1" target="$2" i
    for i in ${SKILL_NAMES[@]+"${!SKILL_NAMES[@]}"}; do
        if [[ "${SKILL_NAMES[$i]}" == "$name" ]]; then
            fail "duplicate skill '$name': ${SKILL_TARGETS[$i]} vs $target"
            fail "disable one source in manifest.yml or rename one skill directory"
            return
        fi
    done
    SKILL_NAMES+=("$name")
    SKILL_TARGETS+=("$target")
}

for dir in "$HOME_SKILLS_DIR"/*/; do
    [[ -f "$dir/SKILL.md" ]] || continue
    add_skill "$(basename "$dir")" "../home-skills/$(basename "$dir")"
done

for repo in ${ENABLED_REPOS[@]+"${ENABLED_REPOS[@]}"}; do
    for dir in "$REPOS_DIR/$repo"/skills/*/; do
        [[ -f "$dir/SKILL.md" ]] || continue
        add_skill "$(basename "$dir")" "../repos/$repo/skills/$(basename "$dir")"
    done
done

if [[ $FAILURES -gt 0 ]]; then
    say ""
    say "ERROR: aborting before catalogue changes ($FAILURES problem(s) above)."
    exit 1
fi

desired_target() {   # -> the wanted symlink target for a name, or ""
    local name="$1" i
    for i in ${SKILL_NAMES[@]+"${!SKILL_NAMES[@]}"}; do
        [[ "${SKILL_NAMES[$i]}" == "$name" ]] && { echo "${SKILL_TARGETS[$i]}"; return; }
    done
    echo ""
}

# Prune stale owned symlinks. Owned = raw target points into home-skills/ or
# repos/. Anything else in skills/ (real dirs, foreign symlinks, dotfiles such
# as harness-installed .system/) is never touched.
for entry in "$CATALOGUE_DIR"/*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="$(basename "$entry")"
    [[ -L "$entry" ]] || continue
    raw="$(readlink "$entry")"
    case "$raw" in
        ../home-skills/*|../repos/*)
            if [[ "$raw" != "$(desired_target "$name")" ]]; then
                act rm "$entry"
                note "$name: removed stale link -> $raw"
            fi
            ;;
    esac
done

# Create/verify desired links.
for i in ${SKILL_NAMES[@]+"${!SKILL_NAMES[@]}"}; do
    name="${SKILL_NAMES[$i]}"
    target="${SKILL_TARGETS[$i]}"
    entry="$CATALOGUE_DIR/$name"

    if [[ -L "$entry" ]]; then
        if [[ "$(readlink "$entry")" == "$target" ]]; then
            note "$name -> $target (ok)"
        elif [[ $CHECK -eq 1 ]]; then
            fail "$name points to $(readlink "$entry"), expected $target"
        else
            # Foreign symlink occupying a desired name.
            if [[ $FORCE -eq 1 ]]; then
                act rm "$entry" && act ln -s "$target" "$entry"
                note "$name -> $target (replaced foreign link)"
            elif [[ $BACKUP -eq 1 ]]; then
                act mv "$entry" "$entry.backup-$STAMP"
                act ln -s "$target" "$entry"
                note "$name -> $target (old link backed up)"
            else
                fail "$name is a foreign symlink -> $(readlink "$entry"); rerun with --force or --backup"
            fi
        fi
    elif [[ -e "$entry" ]]; then
        if [[ $CHECK -eq 1 ]]; then
            fail "$name exists as a real file/directory, expected symlink -> $target"
        elif [[ $BACKUP -eq 1 ]]; then
            act mv "$entry" "$entry.backup-$STAMP"
            act ln -s "$target" "$entry"
            note "$name -> $target (real entry backed up to $name.backup-$STAMP)"
        else
            # Never delete real skill data, not even with --force.
            fail "$name is a real file/directory; rerun with --backup (setup never deletes real skill directories)"
        fi
    elif [[ $CHECK -eq 1 ]]; then
        fail "$name missing (expected -> $target)"
    else
        act ln -s "$target" "$entry"
        note "$name -> $target (created)"
    fi
done

# ── 4. Tool links ────────────────────────────────────────────────────────────

# ensure_link <src> <dst> — create/verify symlink with conflict policy:
# correct link: skip; wrong link/file: fail unless --force (replace) or
# --backup (move aside); real directory: fail unless --backup.
ensure_link() {
    local src="$1" dst="$2"
    if [[ -L "$dst" ]]; then
        if [[ "$(readlink "$dst")" == "$src" ]]; then
            note "$dst -> $src (ok)"
        elif [[ $CHECK -eq 1 ]]; then
            fail "$dst points to $(readlink "$dst"), expected $src"
        elif [[ $FORCE -eq 1 ]]; then
            act rm "$dst" && act ln -s "$src" "$dst"
            note "$dst -> $src (replaced old link)"
        elif [[ $BACKUP -eq 1 ]]; then
            act mv "$dst" "$dst.backup-$STAMP" && act ln -s "$src" "$dst"
            note "$dst -> $src (old link backed up)"
        else
            fail "$dst points to $(readlink "$dst"); rerun with --force or --backup"
        fi
    elif [[ -d "$dst" ]]; then
        if [[ $CHECK -eq 1 ]]; then
            fail "$dst is a real directory, expected symlink -> $src"
        elif [[ $BACKUP -eq 1 ]]; then
            act mv "$dst" "$dst.backup-$STAMP" && act ln -s "$src" "$dst"
            note "$dst -> $src (directory backed up to $(basename "$dst").backup-$STAMP)"
        else
            fail "$dst is a real directory; rerun with --backup (setup never deletes directories)"
        fi
    elif [[ -e "$dst" ]]; then
        if [[ $CHECK -eq 1 ]]; then
            fail "$dst is a real file, expected symlink -> $src"
        elif [[ $FORCE -eq 1 ]]; then
            act rm "$dst" && act ln -s "$src" "$dst"
            note "$dst -> $src (replaced file)"
        elif [[ $BACKUP -eq 1 ]]; then
            act mv "$dst" "$dst.backup-$STAMP" && act ln -s "$src" "$dst"
            note "$dst -> $src (file backed up)"
        else
            fail "$dst exists as a real file; rerun with --force or --backup"
        fi
    elif [[ $CHECK -eq 1 ]]; then
        fail "$dst missing (expected -> $src)"
    else
        act ln -s "$src" "$dst"
        note "$dst -> $src (created)"
    fi
}

say ""
say "Tool links..."

for record in ${TOOL_RECORDS[@]+"${TOOL_RECORDS[@]}"}; do
    name="$(field "$record" name '?')"
    config_dir="$(expand_tilde "$(field "$record" config_dir)")"
    instructions_link="$(field "$record" instructions_link)"
    skills_link="$(field "$record" skills_link)"

    if [[ -z "$config_dir" || -z "$instructions_link" || -z "$skills_link" ]]; then
        fail "manifest tool entry incomplete: $record"
        continue
    fi

    say ""
    say "    [$name] $config_dir"
    if [[ ! -d "$config_dir" ]]; then
        note "config dir not found — tool not installed, skipping"
        continue
    fi
    ensure_link "$AGENT_HOME/AGENTS.md" "$config_dir/$instructions_link"
    ensure_link "$AGENT_HOME/skills" "$config_dir/$skills_link"
done

# ── Summary ──────────────────────────────────────────────────────────────────

say ""
if [[ $FAILURES -gt 0 ]]; then
    say "=== $MODE finished: $FAILURES problem(s) found ==="
    exit 1
fi
if [[ $DRY_RUN -eq 1 ]]; then
    say "=== dry-run finished: $CHANGES action(s) planned, nothing changed ==="
elif [[ $CHECK -eq 1 ]]; then
    say "=== check finished: everything in place ==="
else
    say "=== setup finished: ok ==="
fi
