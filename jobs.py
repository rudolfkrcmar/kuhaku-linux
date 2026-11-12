"""Several jobs at once: each conversation with Claude is a tab on the home screen.

Jobs wait in one line and run when there is room. Two things decide the room:
the computer's memory (each Claude needs about 450 MB, and the system keeps a
reserve so apps stay quick) and the Claude plan (when the session limit is
nearly used, only what was put first runs; when it is used up, everything
waits for the reset). A job can also wait for a time of day: "at 7:00 …".

Kept in ~/.local/state/kuhaku/tabs.json, so the line survives a restart.
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from kuhaku_log import report  # faults go to the log instead of vanishing

PER_JOB_MB = 450
RESERVE_MB = 1200
FIRST, NORMAL, LATER = 2, 1, 0  # priorities: put first, normal, whenever there is room


def mem_available_mb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        report()
    return 4096


def plan_room(limits):
    """(level, resets): "open", "tight" (only first jobs, one at a time) or "closed" until resets."""
    s = (limits or {}).get("session") or {}
    w = (limits or {}).get("week") or {}
    worst = max(s.get("percent") or 0, w.get("percent") or 0)
    resets = (w if (w.get("percent") or 0) >= (s.get("percent") or 0) else s).get("resets")
    if worst >= 99:
        return "closed", resets
    if worst >= 90:
        return "tight", resets
    return "open", resets


def slots(running, max_jobs, limits):
    """How many more jobs may start now, and why not, if none."""
    level, resets = plan_room(limits)
    if level == "closed":
        return 0, "plan", resets
    by_memory = (mem_available_mb() - RESERVE_MB) // PER_JOB_MB
    if running == 0:
        by_memory = max(by_memory, 1)  # one job always runs, however full the memory is
    cap = 1 if level == "tight" else max_jobs
    room = max(0, min(cap - running, by_memory))
    why = None if room else ("plan" if level == "tight" and running else "memory" if by_memory <= 0 else "busy")
    return room, why, resets


WHEN = re.compile(r"^(?:at|@)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?[,:]?\s+", re.I)


def parse_when(text):
    """ "at 7:00 sort my mail" -> (timestamp of the next 7:00, "sort my mail")."""
    m = WHEN.match(text)
    if not m:
        return None, text
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if h > 23 or mins > 59:
        return None, text
    now = datetime.now()
    at = now.replace(hour=h, minute=mins, second=0, microsecond=0)
    if at <= now:
        at += timedelta(days=1)
    return int(at.timestamp()), text[m.end() :].strip() or text


def model_label(model_id):
    """claude-opus-5-5 -> Opus 5.5, claude-haiku-4-5-20251001 -> Haiku 4.5."""
    parts = [p for p in (model_id or "").removeprefix("claude-").split("-") if not (p.isdigit() and len(p) == 8)]
    return (parts[0].capitalize() + (" " + ".".join(parts[1:]) if parts[1:] else "")) if parts else ""


def title_of(text):
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    t = " ".join(words[:4])
    return (t[:32] + "…") if len(t) > 32 or len(words) > 4 else t


class Tabs:
    def __init__(self, path, first_session=None):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text())
            self.tabs, self.current = data["tabs"], data["current"]
        except Exception:
            self.tabs, self.current = [], None
        if not self.tabs:
            self.tabs = [self.blank(first_session)]
        # A job that was running when Kūhaku stopped goes back into the line, where it continues.
        for t in self.tabs:
            if t["status"] == "running":
                t["status"] = "queued"
                if t.get("session_id") and not t["question"].startswith("Kūhaku restarted"):
                    t["question"] = (
                        "Kūhaku restarted while you were working on this. Continue where you stopped: " + t["question"]
                    )
        if not self.get(self.current):
            self.current = self.tabs[0]["id"]

    def blank(self, session=None):
        n = 1 + max([int(t["id"]) for t in getattr(self, "tabs", [])] or [0])
        return {
            "id": str(n),
            "title": "",
            "session_id": session,
            "status": "idle",
            "priority": NORMAL,
            "question": "",
            "at": None,
            "queued": 0,
            "unseen": False,
        }

    def get(self, ident):
        return next((t for t in self.tabs if t["id"] == ident), None)

    def new(self):
        t = self.blank()
        self.tabs.append(t)
        self.current = t["id"]
        self.save()
        return t

    def close(self, ident):
        t = self.get(ident)
        if not t:
            return
        i = self.tabs.index(t)
        self.tabs.remove(t)
        if not self.tabs:
            self.tabs = [self.blank()]
        if self.current == ident:
            self.current = self.tabs[max(0, i - 1)]["id"]
        self.save()

    def waiting(self):
        """The line, in order: first jobs first, then by when they were asked."""
        now = time.time()
        q = [t for t in self.tabs if t["status"] == "queued" and (not t.get("at") or t["at"] <= now)]
        return sorted(q, key=lambda t: (-t.get("priority", NORMAL), t.get("queued", 0)))

    def running(self):
        return [t for t in self.tabs if t["status"] == "running"]

    def save(self):
        try:
            self.path.write_text(json.dumps({"tabs": self.tabs, "current": self.current}))
        except OSError:
            pass

    def public(self):
        """What the home screen shows: each tab's title and state, and the line's order."""
        line = self.waiting()
        out = []
        for t in self.tabs:
            p = {k: t.get(k) for k in ("id", "title", "status", "priority", "at", "unseen", "note", "agent", "anon")}
            p["place"] = line.index(t) + 1 if t in line else None
            out.append(p)
        return {"tabs": out, "current": self.current}


# --- What each job cost: kept for Kiroku, the history of jobs ---------------
RUNS_FILE = Path.home() / ".local/state/kuhaku/jobs.jsonl"
TICK = 100  # clock ticks per second in /proc (os.sysconf("SC_CLK_TCK") on this kernel)
BATTERY = next(
    (
        p
        for p in sorted(Path("/sys/class/power_supply").glob("*"))
        if (p / "type").exists() and (p / "type").read_text().strip() == "Battery"
    ),
    Path("/sys/class/power_supply/BAT0"),
)
CHIP_WATTS = 4.5  # the Core M's whole design power, used when the battery cannot say


def cpu_seconds(pid):
    """Processor time a program and the tools it waited for have used so far."""
    try:
        f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return sum(int(x) for x in f[11:15]) / TICK
    except Exception:
        report()
        return None


def memory_mb(pid):
    """What a program and everything it started hold in memory now."""
    try:
        import psutil

        p = psutil.Process(pid)
        return sum(x.memory_info().rss for x in [p] + p.children(recursive=True)) / 2**20
    except Exception:
        report()
        return None


def erase_session(config_dir, session_id):
    """An anonymous conversation leaves nothing behind on this computer once its tab closes."""
    import shutil

    if not session_id or not re.fullmatch(r"[0-9a-f-]{36}", session_id):
        return
    root = Path(config_dir)
    for pattern in (
        f"projects/*/{session_id}.jsonl",
        f"projects/*/{session_id}",
        f"todos/{session_id}*",
        f"file-history/{session_id}",
        f"session-env/{session_id}",
        f"debug/{session_id}*",
    ):
        for f in root.glob(pattern):
            try:
                shutil.rmtree(f) if f.is_dir() else f.unlink()
            except OSError:
                pass


def battery_watts():
    """What the whole laptop draws now, when it runs on battery (None on the charger)."""
    try:
        if (BATTERY / "status").read_text().strip() != "Discharging":
            return None
        return int((BATTERY / "current_now").read_text()) * int((BATTERY / "voltage_now").read_text()) / 1e12
    except Exception:
        report()
        return None


def energy_wh(seconds, cpu, watts):
    """The laptop's share: measured on battery, otherwise estimated from processor time."""
    if watts:
        return round(sum(watts) / len(watts) * seconds / 3600, 3), "battery"
    if cpu is not None:
        return round(cpu * CHIP_WATTS / 3600, 4), "estimate"
    return None, None


def log_run(record):
    try:
        with open(RUNS_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass
