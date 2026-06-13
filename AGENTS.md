# Contributor and Agent Guide (heretic-win-AMD)

This file is binding for every human and every AI agent (Claude, Codex, Cursor,
Gemini, or any other) that edits this repository. Read it fully before writing
any code, docs, comments, or commit messages. `CLAUDE.md` mirrors this file so
Claude Code loads it automatically; the rules are identical.

This repository is a fork of p-e-w/heretic. The upstream package under
`src/heretic/` is governed by upstream and is exempt from the em-dash rule
below; do not reflow or rewrite upstream code to satisfy it. The owner's own
edits to that package and every file added by this fork follow these rules in
full.

This repository is PUBLIC. Treat everything you write as if a stranger on the
internet will read it on another machine.

## Non-negotiable rules

### 1. No absolute or machine-specific paths in defaults

- Never hardcode an absolute path as a default or fallback. That means no
  `D:\...`, no `C:\Users\...`, no `/home/...`, no `/opt/...`, no `/mnt/...`,
  and no path that only exists on one person's disk.
- Resolve paths relative to the code or the data directory: use
  `Path(__file__).resolve().parent`, the repo root, the configured data home,
  `%~dp0` in batch files, or an OS special-folder API. Never assume a specific
  file or folder exists on disk.
- Anything machine-specific (a data directory, a model directory, an external
  tool location, a GPU binary directory) is USER CONFIG. Prompt for it on first
  use, read it from the config file or an environment variable, or derive it
  from the install. If it is not configured, resolve to nothing and tell the
  user how to set it. Do not guess an absolute path.
- A path that works only on the author's machine is both a bug (it breaks for
  everyone else) and a disclosure (rule 2).

### 2. No local or personal disclosure in tracked files

Never commit, in code, docs, comments, config, test data, or compiled
artifacts:

- A local username used as a path component, for example `C:\Users\someone\...`.
- A personal email, real name, hostname, or machine name.
- An API key, token, password, private key, or connection string.
- A private project name or an absolute path to another project on disk.

Allowed and not a violation: the public GitHub organization name in URLs, the
LICENSE copyright line, and clearly illustrative placeholder paths in help text
or examples that a USER would set (for example `D:\path\to\thing` shown as "set
this to your install"). When in doubt, make the example generic.

Keep personal and local files out of git with `.gitignore`: `config.toml`,
machine-specific launcher state, the `.venv/`, study checkpoints, the disk
offload folder, and `.claude/`.

### 3. No em-dashes

Do not use the em-dash character (the long dash, Unicode U+2014) anywhere: not
in code comments, docstrings, documentation, README files, or commit messages,
and especially not in public-facing text. Use a comma, a period, a colon,
parentheses, or a spaced hyphen ( - ) instead. The en-dash (U+2013) is also out.
Plain ASCII hyphen-minus only. This rule binds everything this fork writes; the
upstream package under `src/heretic/` is exempt where the owner has not edited
it.

### 4. Self-contained

The project depends only on its own uv-managed `.venv` and its own data
directory. It must not rely on sibling folders or anything else on disk that
will not exist on another machine. Paths derive from the script or install
location (`Path(__file__)` in Python, `%~dp0` in batch files) or from user
config, never from a hardcoded personal path. The gfx1030 ROCm runtime is
provisioned from the `rocm-sdk` wheels in the venv, not from a hardcoded
external folder.

## How these rules are enforced

Run the hygiene check before you commit:

```
python scripts/check_hygiene.py
```

It scans tracked files for absolute or machine-specific paths, the em-dash
character, and personal identifiers, and exits non-zero on a violation. The
upstream package under `src/heretic/` is skipped except for the four
owner-edited files (`config.py`, `main.py`, `model.py`, `utils.py`). Wire it as
a pre-commit hook (`scripts/check_hygiene.py --install-hook`) so a commit that
breaks these rules is blocked, not merely discouraged.

If you discover a violation already in git history, do not only fix it forward.
A non-sensitive bad path can be fixed in a normal commit, but a genuine
disclosure in history (a secret, a personal email, or a real-user absolute path
baked into a committed file or binary) requires a history rewrite and a
force-push, and the maintainer must be told before that happens.
