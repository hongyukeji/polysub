"""Local OpenAI-compatible relay in front of the translation model.

VideoCaptioner can only send standard OpenAI fields, so PolySub points it at
this relay, which:
  * injects the API key and model name of the chosen provider;
  * sets the provider-specific thinking switch (off / low / medium);
  * on content-moderation rejections (or 5xx / network errors) from a cloud
    provider, retries the same request on the local fallback model, so a
    film never fails half way because one batch was blocked.

Configured by environment variables (set by bin/polysub):
  LP_PORT                      listen port
  LP_UPSTREAM, LP_KEY, LP_MODEL, LP_STYLE   primary (style: local|qwen|deepseek)
  LP_THINK                     off | low | medium
  LP_FB_UPSTREAM, LP_FB_KEY, LP_FB_MODEL    optional fallback (always style local)
  LP_STATS                     file that receives one line per fallback
Upstreams are base URLs without /v1, e.g. http://127.0.0.1:8888
"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

E = os.environ
PORT = int(E.get("LP_PORT", "8889"))
PRIMARY = dict(up=E["LP_UPSTREAM"].rstrip("/"), key=E.get("LP_KEY", ""),
               model=E.get("LP_MODEL", ""), style=E.get("LP_STYLE", "local"))
FALLBACK = None
if E.get("LP_FB_UPSTREAM"):
    FALLBACK = dict(up=E["LP_FB_UPSTREAM"].rstrip("/"), key=E.get("LP_FB_KEY", ""),
                    model=E.get("LP_FB_MODEL", ""), style="local")
THINK = E.get("LP_THINK", "off")
STATS = E.get("LP_STATS")
BUDGET = {"low": 2048, "medium": 8192}  # qwen thinking_budget per level


def shape(body, t):
    """Apply model name and thinking switch for target t."""
    d = dict(body)
    if t["model"]:
        d["model"] = t["model"]
    if t["style"] == "local":  # oMLX / vLLM-style chat template kwargs
        kw = dict(d.get("chat_template_kwargs") or {})
        kw["enable_thinking"] = THINK != "off"
        if THINK != "off":
            kw["reasoning_effort"] = THINK
        d["chat_template_kwargs"] = kw
    elif t["style"] == "qwen":  # Alibaba Bailian compatible mode
        d["enable_thinking"] = THINK != "off"
        if THINK != "off":
            d["thinking_budget"] = BUDGET.get(THINK, 8192)
    elif t["style"] == "deepseek":
        d["thinking"] = {"type": "disabled" if THINK == "off" else "enabled"}
    return d


def call(t, body):
    h = {"Content-Type": "application/json"}
    if t["key"]:
        h["Authorization"] = f"Bearer {t['key']}"
    return requests.post(t["up"] + "/v1/chat/completions", headers=h,
                         data=json.dumps(shape(body, t)), timeout=1800)


def should_fallback(r):
    """Moderation / server errors -> local. Auth, billing, quota -> fail loudly."""
    if r is None:
        return True
    if r.status_code in (401, 402, 403, 429) and b"inspection" not in r.content \
            and b"risk" not in r.content.lower():
        return False
    if r.status_code >= 400:
        return True
    try:
        ch = r.json()["choices"][0]
        return ch.get("finish_reason") == "content_filter" or not (ch["message"].get("content") or "").strip()
    except Exception:
        return True


def note(msg):
    sys.stderr.write(msg + "\n")
    if STATS:
        with open(STATS, "a") as f:
            f.write(msg + "\n")


class H(BaseHTTPRequestHandler):
    def _send(self, code, content, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, b'{"error":"only chat/completions is relayed"}')
        body.pop("stream", None)
        r = None
        for attempt in range(3):
            try:
                r = call(PRIMARY, body)
            except requests.RequestException as e:
                r, err = None, str(e)
            if r is not None and r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            break
        if FALLBACK and should_fallback(r):
            why = f"HTTP {r.status_code}: {r.text[:160]}" if r is not None else f"network: {err[:160]}"
            note(f"fallback -> local ({why})")
            r = call(FALLBACK, body)
        if r is None:
            return self._send(502, json.dumps({"error": err}).encode())
        self._send(r.status_code, r.content, r.headers.get("Content-Type", "application/json"))

    def do_GET(self):  # /v1/models etc. -> primary
        h = {"Authorization": f"Bearer {PRIMARY['key']}"} if PRIMARY["key"] else {}
        r = requests.get(PRIMARY["up"] + self.path, headers=h, timeout=60)
        self._send(r.status_code, r.content, r.headers.get("Content-Type", "application/json"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
