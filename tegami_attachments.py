"""Attachments: listing, previewing, opening and saving them. A part of the Mail class (tegami_core.py)."""

import hashlib
import re
import subprocess
from pathlib import Path

import desktop
from kuhaku_log import report  # faults go to the log instead of vanishing
from tegami_parts import MailParts
from tegami_store import (
    HOME,
    parse,
    split_key,
)


class Attachments(MailParts):
    def attachment(self, key, index):
        """The attachment's name, type and bytes, or None."""
        if key.startswith("outbox/"):
            row = self.store.q("SELECT path FROM outbox WHERE id=?", (key.split("/", 1)[1],))
        else:
            acct, folder, uid = split_key(key)
            row = self.store.q("SELECT path FROM msg WHERE acct=? AND folder=? AND uid=?", (acct, folder, int(uid)))
        if not row:
            return None
        parts = [p for p in parse(Path(row[0][0]).read_bytes()).iter_attachments() if p.get_filename()]
        if not 0 <= index < len(parts):
            return None
        part = parts[index]
        name = re.sub(r'[/\\\x00]', "_", part.get_filename()).strip() or "file"
        return name, part.get_content_type(), part.get_payload(decode=True) or b""

    def preview(self, key, index):
        """Show an attachment inside Tegami: PDF pages as pictures, pictures as they are, text as text."""
        got = self.attachment(key, index)
        if not got:
            return self.later(lambda: self.js("toast", "That file is not in the letter") and False)
        name, ctype, data = got
        folder = HOME / ".cache/tegami/files" / hashlib.sha1(f"{key}/{index}".encode()).hexdigest()[:12]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(data)
        low = name.lower()
        out = {"key": key, "index": index, "name": name, "type": ctype, "size": len(data), "path": str(target)}
        try:
            if ctype == "application/pdf" or low.endswith(".pdf"):
                pages = sorted(folder.glob("page-*.png"))
                if not pages:
                    subprocess.run(
                        ["pdftoppm", "-png", "-r", "110", "-l", "40", str(target), str(folder / "page")],
                        capture_output=True,
                        timeout=90,
                    )
                    pages = sorted(folder.glob("page-*.png"))
                n = 0
                try:
                    info = subprocess.run(["pdfinfo", str(target)], capture_output=True, text=True, timeout=10).stdout
                    n = int((re.search(r"^Pages:\s+(\d+)", info, re.M) or [0, 0])[1])
                except Exception:
                    report()
                out.update(kind="pdf", pages=[p.as_uri() for p in pages], total=n or len(pages))
            elif ctype.startswith("image/"):
                out.update(kind="image", src=target.as_uri())
            elif ctype.startswith("text/") or low.endswith((".txt", ".csv", ".md", ".json", ".log")):
                out.update(kind="text", text=data[:400000].decode("utf-8", "replace"))
            else:
                out.update(kind="other")
        except Exception as e:
            out.update(kind="other", error=str(e)[:120])
        self.later(lambda: self.js("previewed", out) and False)

    def open_file(self, key, index, save=False):
        """An attachment, opened in its app (Miru for PDFs and pictures), or saved to Downloads."""
        got = self.attachment(key, index)
        if not got:
            return self.js("toast", "That file is not in the letter")
        name, ctype, data = got
        if save:
            folder = HOME / "Downloads"
            folder.mkdir(exist_ok=True)
            target, n = folder / name, 1
            while target.exists():
                target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
                n += 1
            target.write_bytes(data)
            return self.js("toast", f"Saved in Downloads as {target.name}")
        folder = HOME / ".cache/tegami/files" / hashlib.sha1(f"{key}/{index}".encode()).hexdigest()[:12]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(data)
        viewable = ctype == "application/pdf" or ctype.split("/")[0] in ("image", "video", "audio", "text")
        desktop.open_file(target, viewable)
        self.js("toast", f"Opening {name}")
