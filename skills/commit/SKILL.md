---
name: commit
description: Use this skill whenever creating or staging a git commit.
---

## Commit Message Format
- Line 1: short imperative summary (under 72 chars)
- Blank line
- Body: rationale + every new/changed/removed file with reason, grouped logically

## Steps
1. Run `git status` and `git diff` to review all changes
2. Stage specific files by name — never `git add -A` or `git add .`
3. Commit using a HEREDOC:

```bash
git commit -m "$(cat <<'EOF'
Short summary

Detailed description

Changed files:
- path/to/file: reason
EOF
)"
```

4. Run `git status` to confirm success
5. Never use `--no-verify` and never amend — if a hook fails, fix the issue and create a new commit
