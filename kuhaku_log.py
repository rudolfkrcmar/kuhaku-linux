"""Kūhaku's error log: faults are written down instead of vanishing.

Many things in Kūhaku are best effort (a battery that is not there, a file that may not exist
yet, a device that just went away) and must never stop an app. Those places call report():
the app carries on, and the fault goes to ~/.local/state/kuhaku/logs/<app>.log, once per
place and kind of error in each run, so a check that repeats every second cannot flood it.
Kansei shows the logs; `KUHAKU_DEBUG=1` also prints them.

    from kuhaku_log import report
    try:
        ...
    except Exception:
        report()          # inside an except block: notes what went wrong, and where
"""
import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

LOG_DIR = Path.home() / ".local/state/kuhaku/logs"
_seen = set()
_logger = None


def app_name():
    main = sys.modules.get("__main__")
    name = Path(getattr(main, "__file__", "") or sys.argv[0] or "kuhaku").stem
    return name.replace("_", "-") or "kuhaku"


def logger():
    global _logger
    if _logger is None:
        _logger = logging.getLogger("kuhaku")
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(LOG_DIR / f"{app_name()}.log",
                                                           maxBytes=500_000, backupCount=1, encoding="utf-8")
        except OSError:
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(handler)
        if os.environ.get("KUHAKU_DEBUG"):
            _logger.addHandler(logging.StreamHandler(sys.stderr))
    return _logger


def report(what=None):
    """Writes down the exception being handled (call it inside an except block). Never raises."""
    try:
        etype, err, tb = sys.exc_info()
        caller = sys._getframe(1)
        where = f"{Path(caller.f_code.co_filename).name}:{caller.f_lineno} {caller.f_code.co_name}"
        key = (where, etype)
        if key in _seen:
            return
        _seen.add(key)
        text = f"{where}: {what + ': ' if what else ''}{etype.__name__ if etype else 'error'}: {err}"
        if tb is not None and not isinstance(err, (OSError, ValueError, KeyError)):
            text += "\n" + "".join(traceback.format_tb(tb, limit=6)).rstrip()
        logger().warning(text)
    except Exception:
        pass  # the log itself must never break an app


def note(text):
    """Writes down something worth knowing that is not an exception."""
    try:
        logger().info(text)
    except Exception:
        pass
