# Working on Kūhaku

- This project is the only place Kūhaku is changed. Never edit the running copy in `~/.local/share/kuhaku`; it is written by `dev/install` alone.
- After a change: `dev/check` (compile, tests, pages, lint), commit, then `dev/install`. Restart Kūhaku to see it.
- If `dev/install` says the running copy was changed directly, run `dev/install --adopt`, review with `git diff`, commit, then install.
- Read ARCHITECTURE.md first: how the apps, the home screen, Claude and the system talk to each other.
- Best-effort code never hides a fault: `except Exception: report()` (from `kuhaku_log`), never a bare `pass`.
- The repository is public: no personal paths, names, accounts or tokens. `tests/test_static.py` checks for home paths.
