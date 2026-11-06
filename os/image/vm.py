#!/usr/bin/env python3
"""Tiny driver for the test VM: `vm.py shot out.png`, `vm.py run "cmd"` (as admin over serial), `vm.py log`."""
import socket, sys, time, json, re
def serial():
    s = socket.socket(socket.AF_UNIX); s.connect("/tmp/kvm/serial.sock"); s.settimeout(0.5); return s
def read_all(s, wait=1.0):
    out = b""; end = time.time() + wait
    while time.time() < end:
        try:
            d = s.recv(65536)
            if d: out += d; end = time.time() + wait
        except socket.timeout:
            pass
    return out.decode(errors="replace")
def run(cmd, wait=4.0):
    s = serial(); s.sendall(b"\n"); t = read_all(s, 1.5)
    if "login:" in t:
        s.sendall(b"admin\n"); read_all(s, 1.5); s.sendall(b"admin\n"); read_all(s, 3)
        s.sendall(b"export TERM=dumb PAGER=cat SYSTEMD_PAGER=cat; stty cols 200\n"); read_all(s, 1)
    marker = "__END%d__" % int(time.time())
    s.sendall((cmd + f"; echo {marker}\n").encode())
    out = ""; end = time.time() + 120
    while time.time() < end and marker not in out.split(cmd)[-1] if cmd in out else time.time() < end and marker not in out:
        out += read_all(s, wait)
        if out.count(marker) >= 2 or (marker in out and cmd not in out): break
    out = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out)
    print(out.split(marker)[-2] if out.count(marker) >= 2 else out)
def qmp(cmd):
    s = socket.socket(socket.AF_UNIX); s.connect("/tmp/kvm/qmp.sock"); f = s.makefile("rw")
    f.readline(); f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n"); f.flush(); f.readline()
    f.write(json.dumps(cmd) + "\n"); f.flush(); return f.readline()
if sys.argv[1] == "shot":
    print(qmp({"execute": "screendump", "arguments": {"filename": sys.argv[2] + ".ppm"}}))
    time.sleep(1)
    from PIL import Image; Image.open(sys.argv[2] + ".ppm").save(sys.argv[2])
elif sys.argv[1] == "run":
    run(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 4.0)
elif sys.argv[1] == "key":
    print(qmp({"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": k} for k in sys.argv[2].split("+")]}}))
