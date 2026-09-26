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
        self.assertEqual(manifest.recommended_tier(16 * 1024 ** 3), "standard")
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


class QwenAsr(unittest.TestCase):
    def test_parse(self):
        from polysub.api import parse_qwen3_asr
        self.assertEqual(parse_qwen3_asr("language Japanese<asr_text>今日もよろしく。"), ("今日もよろしく。", "ja"))
        self.assertEqual(parse_qwen3_asr("language None<asr_text>"), ("", ""))
        self.assertEqual(parse_qwen3_asr("plain text"), ("plain text", ""))

    def test_two_files_and_backend(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"POLYSUB_MODELS": d}):
            m = manifest.MODELS["asr-qwen3"]
            names = [n for n, _, _ in manifest.files(m)]
            self.assertEqual(names, [m.file, m.mmproj])
            self.assertEqual(manifest.engine_of("asr-qwen3"), ("llama", os.path.join(d, m.mmproj)))
            self.assertEqual(manifest.engine_of("asr-turbo")[0], "whisper")
            open(os.path.join(d, m.file), "wb").write(b"x")
            self.assertFalse(manifest.is_installed("asr-qwen3"))          # projector still missing
            open(os.path.join(d, m.mmproj), "wb").write(b"x")
            self.assertTrue(manifest.is_installed("asr-qwen3"))
            own = os.path.join(d, "mine", "asr.gguf"); os.makedirs(os.path.dirname(own))
            open(own, "wb").write(b"x"); open(os.path.join(d, "mine", "mmproj-asr.gguf"), "wb").write(b"x")
            self.assertEqual(manifest.engine_of(own), ("llama", os.path.join(d, "mine", "mmproj-asr.gguf")))

    def test_server_args(self):
        a = runtime.server_args("asr", "/m.gguf", 9, engine="llama", mmproj="/p.gguf")
        self.assertIn("--mmproj", a); self.assertNotIn("--inference-path", a)
        self.assertIn("--inference-path", runtime.server_args("asr", "/m.bin", 9))

    def test_tiers(self):
        g = 1024 ** 3
        self.assertEqual([manifest.recommended_tier(x * g) for x in (8, 16, 64)], ["light", "standard", "high"])

    def test_old_whisper_config_moves_to_qwen3_asr(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            c = config.default_config("standard")
            c.asr.model, c.general.config_version = "asr-turbo", 2
            c.translate.endpoint = "本机 oMLX"      # a deliberate v2 choice must survive
            config.save(c, path)
            c = config.load(path)
            self.assertEqual(c.asr.model, manifest.TIERS[manifest.recommended_tier()][0])
            self.assertEqual(c.translate.endpoint, "本机 oMLX")


class _AsrChat(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(body)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        chunk = {"choices": [{"delta": {"content": "language Japanese<asr_text>部長、お願いします。"}}]}
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode())

    def log_message(self, *a):
        pass


class AsrChatClient(unittest.TestCase):
    def test_builtin_qwen3_asr_goes_through_chat(self):
        from polysub.api import AsrClient
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _AsrChat)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ep = config.Endpoint(name="b", preset="builtin", base_url=f"http://127.0.0.1:{srv.server_port}")
            text, lang = AsrClient(ep, "asr-qwen3").transcribe(b"RIFF", prompt="部長")
            self.assertEqual((text, lang), ("部長、お願いします。", "ja"))
            msgs = _AsrChat.seen[-1]["messages"]
            self.assertEqual(msgs[0], {"role": "system", "content": "部長"})
            self.assertEqual(msgs[1]["content"][0]["type"], "input_audio")
        finally:
            srv.shutdown()


class ModelFolder(unittest.TestCase):
    def tearDown(self):
        manifest.set_models_dir("")
        manifest.find_existing.cache_clear()

    def test_custom_folder_from_config(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POLYSUB_MODELS", None)
            path = os.path.join(d, "config.toml")
            c = config.default_config("standard"); c.general.models_dir = os.path.join(d, "m")
            config.save(c, path)
            config.load(path)
            self.assertEqual(manifest.models_dir(), os.path.join(d, "m"))
            self.assertTrue(manifest.path_of("mt-4b").startswith(os.path.join(d, "m")))

    def test_reuses_a_copy_another_tool_downloaded(self):
        m = manifest.MODELS["mt-1.7b"]
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as mine, \
                mock.patch.dict(os.environ, {"POLYSUB_MODELS": mine}), \
                mock.patch.object(manifest, "other_dirs", lambda: [os.path.join(home, "lms")]):
            manifest.find_existing.cache_clear()
            other = os.path.join(home, "lms", "Qwen", "Qwen3-1.7B-GGUF", m.file)
            os.makedirs(os.path.dirname(other))
            with open(other, "wb") as f:
                f.truncate(m.size)            # same name and size: used, not downloaded again
            self.assertEqual(manifest.path_of("mt-1.7b"), other)
            self.assertTrue(manifest.is_installed("mt-1.7b"))
            manifest.find_existing.cache_clear()
            with open(other, "wb") as f:
                f.truncate(10)                # different size: not the same file
            self.assertEqual(manifest.path_of("mt-1.7b"), os.path.join(mine, m.file))

    def test_no_welcome_when_my_models_are_on(self):
        from polysub.gui.welcome import needs_welcome
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"POLYSUB_MODELS": d}):
            c = config.default_config("standard")
            self.assertTrue(needs_welcome(c))
            c.mine.translate_endpoint, c.mine.translate_model = "本机 oMLX", "big"
            c.general.use_mine = True
            self.assertFalse(needs_welcome(c))


class MigrateV5(unittest.TestCase):
    def test_old_batch_default_becomes_auto(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            c = config.default_config("standard")
            c.general.config_version = 4
            c.translate.batch_size, c.translate.lookahead_lines = 20, 5
            c.find_endpoint(config.BUILTIN).concurrency = 1
            config.save(c, path)
            c = config.load(path)
            self.assertEqual((c.translate.batch_lines(), c.translate.lookahead_lines), (1, 2))
            self.assertEqual(c.find_endpoint(config.BUILTIN).concurrency, 4)
