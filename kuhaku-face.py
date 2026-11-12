#!/usr/bin/env python3
"""Face unlock for Kūhaku's lock screen, with the infrared camera only.

    kuhaku-face.py status          what is set up, as one line of JSON
    kuhaku-face.py enroll          a quiet window: look at the screen, Kūhaku learns your face
    kuhaku-face.py verify [secs]   exit 0 when the person who set it up is in front of the camera
    kuhaku-face.py forget          forget the face

A convenience, not a lock: a password or a finger is stronger. Infrared makes a printed photo or a
phone screen much harder to use, but it is not proof. The camera is only opened while enrolling or
verifying, and closed the moment that ends. Faces are kept as numbers (SFace embeddings) in
~/.local/share/kuhaku/face, readable only by you; no picture is stored.

Models: YuNet (face finder) and SFace (face recognizer) from github.com/opencv/opencv_zoo,
checked against the checksums below before use.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from kuhaku_log import report  # faults go to the log instead of vanishing

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
FACE = HERE / "face"
STORE = FACE / "embeddings.npy"
BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
YUNET = (
    "face_detection_yunet_2023mar.onnx",
    "face_detection_yunet/",
    "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
)
SFACE = (
    "face_recognition_sface_2021dec.onnx",
    "face_recognition_sface/",
    "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
)
# SFace's own cosine threshold is 0.363; Kūhaku asks for more, and for several frames in a row.
THRESHOLD = 0.45
IN_A_ROW = 3
DARK = 22  # mean brightness below this: shutter closed, or the infrared light is off


def ir_camera():
    """/dev/videoN of the infrared camera (its name ends in "I", e.g. "Integrated I"), or None."""
    found = []
    for d in sorted(Path("/sys/class/video4linux").glob("video*"), key=lambda p: int(p.name[5:])):
        try:
            name = (d / "name").read_text().strip()
            index = int((d / "index").read_text().strip()) if (d / "index").exists() else 0
        except Exception:
            report()
            continue
        if index == 0 and (name.endswith(": Integrated I") or " IR " in f" {name} " or "Infrared" in name):
            found.append(int(d.name[5:]))
    return found[0] if found else None


def model(spec):
    """The model file, fetched once into models/ and checked; None if it cannot be had."""
    name, sub, digest = spec
    p = MODELS / name
    if not p.exists():
        import subprocess

        MODELS.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        if subprocess.run(["curl", "-fsSL", "--max-time", "120", "-o", str(tmp), BASE + sub + name]).returncode != 0:
            tmp.unlink(missing_ok=True)
            return None
        tmp.rename(p)
    if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
        p.unlink(missing_ok=True)
        return None
    return str(p)


class Eye:
    """The infrared camera with the face finder and recognizer; opened and closed as one."""

    def __init__(self):
        import cv2

        self.cv2 = cv2
        dev = ir_camera()
        if dev is None:
            raise RuntimeError("no infrared camera")
        y, s = model(YUNET), model(SFACE)
        if not y or not s:
            raise RuntimeError("the face models could not be fetched")
        self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError("the camera is busy")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
        self.detector = cv2.FaceDetectorYN.create(y, "", (640, 360), 0.8, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(s, "")

    def look(self):
        """One frame: (embedding or None, why). why is "", "dark" or "noface"."""
        cv2 = self.cv2
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None, "dark"
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.mean() < DARK:
            return None, "dark"
        h, w = frame.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(frame)
        if faces is None or len(faces) == 0:
            return None, "noface"
        face = max(faces, key=lambda f: f[2] * f[3])  # the nearest face
        if face[2] < w * 0.12:  # too far away to be sure
            return None, "noface"
        aligned = self.recognizer.alignCrop(frame, face)
        return self.recognizer.feature(aligned).flatten(), ""

    def close(self):
        try:
            self.cap.release()
        except Exception:
            report()


def known():
    try:
        import numpy as np

        return np.load(STORE)
    except Exception:
        report()
        return None


def similarity(feature, known_set):
    import numpy as np

    f = feature / (np.linalg.norm(feature) + 1e-9)
    k = known_set / (np.linalg.norm(known_set, axis=1, keepdims=True) + 1e-9)
    return float((k @ f).max())


def status():
    try:
        import cv2  # noqa: F401

        has_cv = True
    except Exception:
        has_cv = False
    return {"camera": ir_camera() is not None, "ready": has_cv, "enrolled": STORE.exists()}


def verify(seconds=10.0):
    """True when the enrolled face is seen IN_A_ROW frames running within `seconds`."""
    saved = known()
    if saved is None:
        return False
    try:
        eye = Eye()
    except Exception:
        report()
        return False
    try:
        run, end = 0, time.time() + seconds
        while time.time() < end:
            feature, _ = eye.look()
            run = run + 1 if feature is not None and similarity(feature, saved) >= THRESHOLD else 0
            if run >= IN_A_ROW:
                return True
        return False
    finally:
        eye.close()


def forget():
    STORE.unlink(missing_ok=True)


def enroll_window():
    import threading
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    sys.path.insert(0, str(HERE))
    from kuhaku_unlock_style import window_css

    want = 20
    win = Gtk.Window(title="Face")
    win.set_decorated(False)
    win.fullscreen()
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), window_css(), Gtk.STYLE_PROVIDER_PRIORITY_USER)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    row = Gtk.Box(spacing=18)
    line = Gtk.Box(name="line")
    line.set_size_request(2, 40)
    row.pack_start(line, False, False, 0)
    big = Gtk.Label(label="Look at the screen", xalign=0)
    row.pack_start(big, False, False, 0)
    box.pack_start(row, False, False, 0)
    small = Gtk.Label(label="Turn your head a little, slowly. Esc to stop.", name="small", xalign=0)
    small.set_margin_start(20)
    box.pack_start(small, False, False, 0)
    dots = Gtk.Label(label="", name="dots", xalign=0)
    dots.set_margin_start(20)
    box.pack_start(dots, False, False, 0)
    note = Gtk.Label(
        label="Face unlock is a convenience. Your password and fingers stay stronger.", name="small", xalign=0
    )
    note.set_margin_start(20)
    note.set_margin_top(30)
    box.pack_start(note, False, False, 0)
    win.add(box)
    stop = threading.Event()

    def say(b, s=None, n=None):
        big.set_text(b)
        if s is not None:
            small.set_text(s)
        if n is not None:
            dots.set_text("".join("●" if i < n else "○" for i in range(want)))
        return False

    def quit_(*_):
        stop.set()
        Gtk.main_quit()

    win.connect("destroy", quit_)
    win.connect("key-press-event", lambda w, e: e.keyval == Gdk.KEY_Escape and w.destroy())
    win.show_all()
    say("Look at the screen", n=0)

    def work():
        import numpy as np

        try:
            eye = Eye()
        except Exception as e:
            GLib.idle_add(say, "The camera is not ready", str(e).capitalize() + ".")
            return
        got, dark_since, last = [], time.time(), 0.0
        try:
            while not stop.is_set() and len(got) < want:
                feature, why = eye.look()
                if why == "dark":
                    if time.time() - dark_since > 3:
                        GLib.idle_add(
                            say,
                            "The camera sees nothing",
                            "Slide the camera cover open. If it is open, the infrared light is off.",
                        )
                    continue
                dark_since = time.time()
                if feature is None:
                    GLib.idle_add(say, "Look at the screen", "Your face, about an arm's length away.")
                    continue
                if time.time() - last < 0.25:  # a little apart, so the views differ
                    continue
                last = time.time()
                got.append(feature)
                GLib.idle_add(say, "Hold on", "Turn your head a little, slowly.", len(got))
        finally:
            eye.close()
        if len(got) < want:
            return
        FACE.mkdir(parents=True, exist_ok=True)
        os.chmod(FACE, 0o700)
        np.save(STORE, np.array(got, dtype=np.float32))
        os.chmod(STORE, 0o600)
        GLib.idle_add(say, "Done", "The lock screen now also opens when it sees you.", want)
        GLib.timeout_add(2500, win.destroy)

    threading.Thread(target=work, daemon=True).start()
    Gtk.main()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status()))
    elif cmd == "verify":
        sys.exit(0 if verify(float(sys.argv[2]) if len(sys.argv) > 2 else 10.0) else 1)
    elif cmd == "forget":
        forget()
    elif cmd == "enroll":
        enroll_window()
    else:
        print(__doc__)
        sys.exit(2)
