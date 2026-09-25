"""Built-in engine runtime with a fake server: start, reuse, restart, crash, idle exit;
model manifest and sha256-checked downloads against a local HTTP server."""
import hashlib
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from polysub import config, models
from polysub.engine import builtin, manifest, runtime

FAKE = textwrap.dedent('''
    import sys, http.server
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1])
    if "crash" in args[args.index("-m") + 1]:
        sys.exit(3)
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
''')


class Runtime(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = d.name
        exe = os.path.join(d.name, "llama-server")
        with open(exe, "w") as f:
            f.write(f"#!{sys.executable}\n" + FAKE)
        os.chmod(exe, 0o755)
        os.symlink(exe, os.path.join(d.name, "whisper-server"))
        self.model = os.path.join(d.name, "a.gguf")
        open(self.model, "w").close()
        env = {"POLYSUB_ENGINES": d.name, "POLYSUB_ENGINE_STATE": os.path.join(d.name, "state"),
               "POLYSUB_ENGINE_IDLE": "60", "PYTHONPATH": os.path.dirname(os.path.dirname(os.path.abspath(
                   __import__("polysub").__file__)))}
        p = mock.patch.dict(os.environ, env)
        p.start(); self.addCleanup(p.stop)
        self.addCleanup(lambda: [runtime.stop(k) for k in runtime.BINARIES])

    def test_start_reuse_and_stop(self):
        u = runtime.ensure("mt", self.model)
        pid = runtime.read_state("mt")["pid"]
        self.assertTrue(runtime.healthy(int(u.rsplit(":", 1)[1])))
        self.assertEqual(runtime.ensure("mt", self.model), u)              # reused
        self.assertEqual(runtime.read_state("mt")["pid"], pid)
        runtime.stop("mt")
        self.assertEqual(runtime.status(), {})
        self.assertFalse(runtime._alive(pid))

    def test_other_model_restarts(self):
        other = os.path.join(self.dir, "b.gguf"); open(other, "w").close()
        runtime.ensure("mt", self.model)
        pid = runtime.read_state("mt")["pid"]
        runtime.ensure("mt", other)
        self.assertNotEqual(runtime.read_state("mt")["pid"], pid)
        self.assertEqual(runtime.read_state("mt")["model"], other)

    def test_exclusive_stops_the_other_kind(self):
        runtime.ensure("asr", self.model)
        runtime.ensure("mt", self.model, exclusive=True)
        self.assertEqual(set(runtime.status()), {"mt"})

    def test_crash_reports_error_and_clears_state(self):
        bad = os.path.join(self.dir, "crash.gguf"); open(bad, "w").close()
        with self.assertRaises(runtime.EngineError):
            runtime.ensure("mt", bad)
        self.assertEqual(runtime.read_state("mt"), {})

    def test_missing_model_file(self):
        with self.assertRaises(runtime.EngineError):
            runtime.ensure("mt", os.path.join(self.dir, "none.gguf"))

    def test_idle_server_stops_itself(self):
        os.environ["POLYSUB_ENGINE_IDLE"] = "1"
        runtime.ensure("asr", self.model)
        pid = runtime.read_state("asr")["pid"]
        with runtime.keepalive(["asr"], every=0.3):
            time.sleep(2)
            self.assertTrue(runtime._alive(pid))                             # kept alive while in use
        for _ in range(40):
            if not runtime._alive(pid):
                break
            time.sleep(0.25)
        self.assertFalse(runtime._alive(pid))
        self.assertEqual(runtime.read_state("asr"), {})

    def test_builtin_start_points_endpoint_at_server(self):
        ep = config.Endpoint(name="内置（本机）", preset="builtin")
        with mock.patch.object(builtin, "small_memory", return_value=False):
            builtin.start(ep, "mt", self.model)
        self.assertTrue(ep.base_url.startswith("http://127.0.0.1:"))


class Manifest(unittest.TestCase):
    def test_tiers_and_resolve(self):
        self.assertEqual(manifest.recommended_tier(8 * 1024 ** 3), "light")
        self.assertEqual(manifest.recommended_tier(32 * 1024 ** 3), "standard")
        for asr_id, mt_id in manifest.TIERS.values():
            self.assertEqual((manifest.MODELS[asr_id].kind, manifest.MODELS[mt_id].kind), ("asr", "mt"))
        self.assertTrue(manifest.resolve("mt-4b").endswith(manifest.MODELS["mt-4b"].file))
        self.assertEqual(manifest.resolve("/x/my.gguf"), "/x/my.gguf")
        with self.assertRaises(KeyError):
            manifest.resolve("nope")

    def test_new_config_defaults(self):
        c = config.default_config("light")
        self.assertEqual((c.asr.endpoint, c.translate.endpoint), (config.BUILTIN, config.BUILTIN))
        self.assertEqual((c.asr.model, c.translate.model), manifest.TIERS["light"])
        self.assertEqual(c.general.config_version, config.CONFIG_VERSION)

    def test_old_config_moves_to_builtin_and_keeps_its_models_as_mine(self):
        old = """[general]
target_langs = ["zh-Hans"]
[asr]
endpoint = "本机 oMLX"
model = "Qwen3-ASR-1.7B-8bit"
[translate]
endpoint = "本机 oMLX"
model = "big-model"
think = "low"
think_budget = 1024
[[endpoints]]
name = "本机 oMLX"
preset = "omlx"
base_url = "http://127.0.0.1:8888"
"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(old)
            c = config.load(path)
            self.assertEqual((c.asr.endpoint, c.translate.endpoint), (config.BUILTIN, config.BUILTIN))
            self.assertIn(config.BUILTIN, [e.name for e in c.endpoints])
            m = c.mine
            self.assertEqual((m.asr_endpoint, m.asr_model, m.translate_endpoint, m.translate_model),
                             ("本机 oMLX", "Qwen3-ASR-1.7B-8bit", "本机 oMLX", "big-model"))
            self.assertEqual((m.think, m.think_budget), ("low", 1024))
            self.assertEqual(config.quality_of(c), "fast")
            again = config.load(path)                                   # saved once, not migrated twice
            self.assertEqual(again.general.config_version, config.CONFIG_VERSION)
            again.translate.endpoint = "本机 oMLX"
            config.save(again, path)
            self.assertEqual(config.load(path).translate.endpoint, "本机 oMLX")   # a later choice sticks


class _Hub(BaseHTTPRequestHandler):
    data = b"x" * 5000
    ranges = []

    def do_GET(self):
        if self.path.startswith("/api/models/"):
            body = json.dumps([{"path": "m.bin", "size": len(self.data),
                                "lfs": {"oid": hashlib.sha256(self.data).hexdigest(), "size": len(self.data)}}]).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
            return
        start = int(self.headers.get("Range", "bytes=0-")[6:].rstrip("-") or 0)
        self.ranges.append(start)
        self.send_response(206 if start else 200); self.end_headers(); self.wfile.write(self.data[start:])

    def log_message(self, *a):
        pass


class Download(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.host = f"http://127.0.0.1:{self.srv.server_address[1]}"
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.dest = os.path.join(d.name, "m.bin")
        _Hub.ranges = []

    def test_resume_and_verify(self):
        with open(self.dest + ".part", "wb") as f:
            f.write(_Hub.data[:1234])
        with mock.patch.object(models, "hosts", return_value=[self.host]):
            models.download_file("r/x", "m.bin", self.dest)
        self.assertEqual(_Hub.ranges, [1234])
        with open(self.dest, "rb") as f:
            self.assertEqual(f.read(), _Hub.data)

    def test_bad_checksum_is_deleted(self):
        with mock.patch.object(models, "hosts", return_value=[self.host]):
            with self.assertRaises(ValueError):
                models.download_file("r/x", "m.bin", self.dest, sha256="0" * 64)
        self.assertFalse(os.path.exists(self.dest) or os.path.exists(self.dest + ".part"))

    def test_falls_back_to_next_host(self):
        with mock.patch.object(models, "hosts", return_value=["http://127.0.0.1:9", self.host]):
            models.download_file("r/x", "m.bin", self.dest)
        self.assertTrue(os.path.isfile(self.dest))


if __name__ == "__main__":
    unittest.main()


class Watch(unittest.TestCase):
    def test_baseline_then_new_settled_files(self):
        from polysub import jobs, watch
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as state:
            with mock.patch.object(jobs, "STATE", state):
                old = os.path.join(d, "old.mp4"); open(old, "w").close()
                self.assertEqual(watch.scan(d), [])                 # first scan: remember, queue nothing
                new = os.path.join(d, "sub", "new.mkv"); os.makedirs(os.path.dirname(new)); open(new, "w").close()
                self.assertEqual(watch.scan(d), [])                 # still being written (just modified)
                past = time.time() - 60
                os.utime(new, (past, past))
                self.assertEqual(watch.scan(d), [new])
                self.assertEqual(watch.scan(d), [])                 # only once
                open(os.path.join(d, "notes.txt"), "w").close()
                self.assertEqual(watch.scan(d, settle=0), [])       # not a video
