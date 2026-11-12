"""What every part of the home screen window may rely on.

The Kuhaku window class (kuhaku.py) is assembled from parts kept in their own files by
subject: Panels, Voice, Settings and ClaudeJobs. Each part uses state and methods that
another part (or kuhaku.py) provides. They are declared here once, so the type checker
knows them and a reader can see what the parts share. Nothing here runs: the real
values are set in Kuhaku.__init__ and the methods are defined in kuhaku.py and the parts.
"""

import subprocess
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import jobs


class Home:
    config: dict[str, Any]  # ~/.config/kuhaku/config.json
    state: dict[str, Any]  # what Kūhaku remembers between runs (state.json)
    tabs: "jobs.Tabs"  # every conversation is a tab
    procs: dict[str, subprocess.Popen]  # running Claude jobs by id
    live: dict[str, Any]  # what each running job has said so far
    claude: str  # the claude command
    window: Any  # the Gtk.Window
    convo: float  # when the spoken conversation on now began, its number (0: off)
    speaker_lock: threading.Lock
    whisper_lock: threading.Lock
    recorder: subprocess.Popen | None
    speaker: subprocess.Popen | None
    voice_proc: subprocess.Popen | None
    whisper: subprocess.Popen | None

    if TYPE_CHECKING:

        def js(self, code: str) -> None: ...
        def send(self, fn: str, payload: Any) -> None: ...
        def note(self, title: str, detail: str) -> None: ...
        def push_status(self) -> None: ...
        def push_theme(self) -> None: ...
        def save_state(self) -> None: ...
        def switch_account(self, ident: str | None = None) -> Any: ...
        def sign_in(self, ident: str, then_use: bool = False) -> Any: ...
        def read_prefs(self) -> dict: ...
        def write_pref(self, key: str, value: Any) -> Any: ...
        def speak_then_listen(self, text: str, conv: int) -> Any: ...
