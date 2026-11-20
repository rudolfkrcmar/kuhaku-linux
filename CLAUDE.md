# Working on Kūhaku

- This project is the only place Kūhaku is changed. Never edit the running copy in `~/.local/share/kuhaku`; it is written by `dev/install` alone.
- After a change: `dev/check` (compile, tests, pages, lint), commit, push, then `dev/install`. Restart Kūhaku to see it.
- System part (login session, packages): `dev/system` builds the package and installs it after one password (`--dry` to preview).
- If `dev/install` says the running copy was changed directly, run `dev/install --adopt`, review with `git diff`, commit, then install.
- Read ARCHITECTURE.md first: how the apps, the home screen, Claude and the system talk to each other.
- Never do slow pure-Python work (parsing big files, long loops) in `kuhaku.py`'s process, not even on a thread: it starves the main loop and freezes the whole screen. Run it in its own process (see "Never stuck" in ARCHITECTURE.md; `calendars.py index` is the example).
- Best-effort code never hides a fault: `except Exception: report()` (from `kuhaku_log`), never a bare `pass`.
- The repository is public: no personal paths, names, accounts or tokens. `tests/test_static.py` checks for home paths.
- Design: read `design/MANUAL.md` (the rules) before drawing an icon, panel or page; the standard is `design/codex.html` ("Kūhaku, the design of blank space"; where the code differs, the code is behind). After changing any icon, run `python3 dev/design.py` and look at `design/library.html`; `dev/check` fails if it is stale.
