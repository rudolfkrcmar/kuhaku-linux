# Contributing to Kūhaku

Thank you for wanting to help. Kūhaku is small on purpose: calm screens, few settings, plain words.

## Getting started

```sh
git clone https://github.com/rudolfkrcmar/kuhaku-linux ~/Projects/kuhaku-linux
cd ~/Projects/kuhaku-linux
git config core.hooksPath dev/hooks      # dev/check quick before every commit
git config blame.ignoreRevsFile .git-blame-ignore-revs
pip install --user ruff mypy             # or let dev/check use Kūhaku's own venv
dev/check                                # everything should say "All good."
```

Read [ARCHITECTURE.md](ARCHITECTURE.md) first: it shows how the pages, the Python behind them, Claude and the system talk to each other.

## Making a change

1. Change the project, never the running copy in `~/.local/share/kuhaku`.
2. Add or update a test: `tests/` for Python, `uitests/` for anything on a page. A new message from a page needs its handler in the `MESSAGES` or `PANEL` table (a test checks).
3. `dev/check` (`dev/check quick` skips the pages and screenshots).
4. Commit with a message that says what changed and why, in plain sentences.
5. `dev/install`, then restart Kūhaku to try it for real.

If a screenshot test fails because the look changed on purpose, accept the new look with `KUHAKU_SHOTS=update python3 -m unittest discover -s uitests -t .` and commit the new pictures with the change.

## House style

- **Words:** the interface speaks plainly and briefly ("Move to Trash", not "Delete item(s)?"). Comments say why, not what.
- **Python:** `ruff format` (120 columns) and `ruff check` decide. Type hints on new public functions. Best-effort code calls `report()` from `kuhaku_log` in its `except`; never a bare `pass`.
- **Pages:** no frameworks; small functions; colours from the CSS variables (`--bg`, `--fg`, `--muted`, `--accent`) so day and night both work. Password and lock screens are always dark.
- **Safety:** commands run as argument lists, never through a shell string. Anything Claude might be tricked into doing that cannot be undone belongs in `kuhaku-guard.py`, with a test.
- **Privacy:** the repository is public. No personal paths, names, accounts or tokens (a test checks for home paths).

## Others' code and licenses

Kūhaku's own code is MIT. Python libraries it needs at run time are bundled, unchanged, in `lib/`
(refresh one with `pip install --target lib --upgrade <name>==<version>`); browser code lives in
`ide/vendor/`. Whatever is added must be under a license on the allowed list in
`tests/test_licenses.py`, ship its license text, and get a row in `THIRD_PARTY_NOTICES.md`. The
test fails until all three are true. Widening the allowed list is a decision, not a fix.

## Releases

Bump `VERSION`, add a section to `CHANGELOG.md`, commit, then tag: `git tag -a v0.3.0 -m "Kūhaku 0.3.0"` and push the tag. `os/release.sh` builds the package and image.
