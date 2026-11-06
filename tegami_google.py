"""Sign in with Google for Tegami: no app passwords.

The usual desktop way (OAuth 2.0 for installed apps, with PKCE): Google's own sign-in
page opens in Mado (on a Mac, the default browser), the person approves, Google hands a code back to Tegami on this
computer (127.0.0.1), and Tegami keeps only a refresh token, in the system keyring.
Mail is then read and sent over IMAP and SMTP with XOAUTH2.

Google lets only registered apps ask for mail access. Tegami is not a company app, so the
owner registers it once in their own Google Cloud project and downloads the small
client file; Tegami notices that file in Downloads by itself.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import desktop

HOME = Path.home()
CLIENT = HOME / ".config" / "kuhaku" / "google-oauth-client.json"
SCOPES = "https://mail.google.com/ openid email profile"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
CONSOLE = "https://console.cloud.google.com/auth/clients/create"

_tokens = {}   # refresh token -> (access token, expiry)

DONE_PAGE = """<!doctype html><meta charset="utf-8"><title>Tegami</title>
<style>html,body{{margin:0;height:100%;background:#121212;color:#ececec;font-family:Inter,system-ui,sans-serif}}
main{{position:absolute;left:50%;top:45%;transform:translateX(-50%);width:min(1160px,calc(100% - 48px));display:flex;gap:18px}}
i{{width:2px;background:#f0705a}}h1{{margin:0;font-size:26px;font-weight:200}}p{{margin:8px 0 0;color:#9a9a9a;font-weight:300}}</style>
<main><i></i><div><h1>{title}</h1><p>{text}</p></div></main>"""


def client():
    """The registered app (client id and secret), from the file Google lets the owner download."""
    if not CLIENT.exists():
        dl = sorted((HOME / "Downloads").glob("client_secret_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in dl:
            try:
                data = json.loads(f.read_text())
                if "installed" in data:
                    CLIENT.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(f, CLIENT)
                    os.chmod(CLIENT, 0o600)
                    f.unlink()     # the secret lives in one place only
                    break
            except Exception:
                continue
    try:
        c = json.loads(CLIENT.read_text())["installed"]
        return {"id": c["client_id"], "secret": c.get("client_secret", "")}
    except Exception:
        return None


def _post(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def sign_in(hint="", timeout=300):
    """Opens Google's sign-in in Mado; returns {"email", "name", "refresh"} or raises."""
    c = client()
    if not c:
        raise RuntimeError("no-client")
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if q.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return
            got.update({k: v[0] for k, v in q.items()})
            ok = "code" in got
            page = DONE_PAGE.format(title="Signed in." if ok else "Not signed in.",
                                    text="Tegami has your mail now. You can close this tab." if ok
                                    else "Google did not give access. Close this tab and try again in Tegami.")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": c["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "access_type": "offline", "prompt": "consent", **({"login_hint": hint} if hint else {})})
    desktop.open_url(url)
    server.timeout = 1
    end = time.time() + timeout
    while not got and time.time() < end:
        server.handle_request()
    server.server_close()
    if "code" not in got:
        raise RuntimeError(got.get("error") or "timeout")
    tok = _post(TOKEN, {"client_id": c["id"], "client_secret": c["secret"], "code": got["code"],
                        "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"})
    if "refresh_token" not in tok:
        raise RuntimeError("Google gave no lasting access")
    req = urllib.request.Request(USERINFO, headers={"Authorization": f"Bearer {tok['access_token']}"})
    who = json.loads(urllib.request.urlopen(req, timeout=20).read())
    _tokens[tok["refresh_token"]] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
    return {"email": who.get("email", ""), "name": who.get("name", ""), "refresh": tok["refresh_token"]}


def access_token(refresh):
    """A fresh access token (an hour long) from the kept refresh token."""
    cached = _tokens.get(refresh)
    if cached and cached[1] > time.time():
        return cached[0]
    c = client()
    if not c:
        raise RuntimeError("no-client")
    tok = _post(TOKEN, {"client_id": c["id"], "client_secret": c["secret"], "refresh_token": refresh,
                        "grant_type": "refresh_token"})
    _tokens[refresh] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3600)) - 60)
    return tok["access_token"]


def xoauth2(email, token):
    return f"user={email}\x01auth=Bearer {token}\x01\x01"
