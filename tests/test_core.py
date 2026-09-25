"""Unit tests (no model needed). Run: .venv/bin/python -m unittest discover -s tests"""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from polysub import config, subtitle
from polysub.api import ChatClient, apply_thinking
from polysub.asr import Cue, _is_echo
from polysub.brief import clean_terms
from polysub.config import Endpoint
from polysub.translate import Translator, _parse


class Terms(unittest.TestCase):
    def test_clean_terms(self):
        self.assertEqual(clean_terms("田中、部長、先生、マネージャー"), "田中、部長、先生、マネージャー")
        self.assertEqual(clean_terms("I'll go with 田中、部長. I only have 2 clear ones."), "")

    def test_echo(self):
        p = "部長、田中、先生、マネージャー"
        self.assertTrue(_is_echo("部长、田中、先生、マネージャー。", p))
        self.assertFalse(_is_echo("田中さんと私は会議室で部長を待っています。", p))
        self.assertFalse(_is_echo("部長、今日もよろしく。", p))


class Parse(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(_parse('```json\n{"1": "a", "2": "b"}\n```', 2), {1: "a", 2: "b"})
        self.assertEqual(_parse('好的：{"1":"a","3":"x","2":""}', 2), {1: "a"})
        self.assertEqual(_parse("not json", 2), {})


class Subtitle(unittest.TestCase):
    def test_wrap_and_split(self):
        cue = Cue(0, 10, "src")
        long_zh = "啊对了，附近有个拉面摊，我打算现在去，要一起去吗？好想去啊！走吧走吧！再说一次吧真的好想去"
        out = subtitle.build([cue], [long_zh], "zh-Hans")
        self.assertEqual(len(out), 2)                       # > 2 lines worth -> two timed cues
        self.assertAlmostEqual(out[0].end, out[1].start)
        self.assertTrue(all(len(l) <= 30 for c in out for l in c.text.split("\n")))
        mid = subtitle.build([cue], ["不好意思啊，部长的会议资料在我包里，我现在得整理一下"], "zh-Hans")
        self.assertEqual(len(mid), 1)
        self.assertIn("\n", mid[0].text)

    def test_script(self):
        self.assertEqual(subtitle.normalize_script("能不能交给部長 高橋", "zh-Hans"), "能不能交给部长 高桥")
        self.assertEqual(subtitle.normalize_script("部長", "en"), "部長")

    def test_write_formats(self):
        with tempfile.TemporaryDirectory() as d:
            for fmt in ("srt", "ass", "vtt"):
                p = subtitle.write([Cue(1.0, 2.5, "你好\n世界")], os.path.join(d, f"x.{fmt}"), fmt)
                self.assertGreater(os.path.getsize(p), 10)


class Config(unittest.TestCase):
    def test_roundtrip_and_perms(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.toml")
            c = config.default_config()
            c.translate.think = "off"
            c.endpoints.append(Endpoint(name="测试", base_url="http://x/v1", api_key="sk-abc"))
            config.save(c, p)
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
            c2 = config.load(p)
            self.assertEqual(c2.translate.think, "off")
            self.assertEqual(c2.endpoint("测试").root, "http://x")


class Thinking(unittest.TestCase):
    def test_styles(self):
        self.assertEqual(apply_thinking({}, "chat_template_kwargs", "off"),
                         {"chat_template_kwargs": {"enable_thinking": False}})
        b = apply_thinking({}, "chat_template_kwargs", "low", 1024)
        self.assertEqual(b["thinking_budget"], 1024)
        self.assertEqual(apply_thinking({}, "enable_thinking", "off"), {"enable_thinking": False})
        self.assertEqual(apply_thinking({}, "deepseek", "off"), {"thinking": {"type": "disabled"}})
        self.assertEqual(apply_thinking({}, "none", "low"), {})


class FakeClient:
    """Answers batches but drops line 2 of every multi-line batch."""
    def __init__(self):
        self.ep = Endpoint(name="fake", concurrency=1)
        self.cancel = threading.Event()
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        lines = json.loads(messages[-1]["content"].split("Translate these lines:\n", 1)[1])
        out = {k: f"T{v}" for k, v in lines.items() if not (len(lines) > 1 and k == "2")}
        return json.dumps(out)


class Translate(unittest.TestCase):
    def test_retry_and_line_fallback(self):
        fc = FakeClient()
        tr = Translator(fc, "ja", "zh-Hans", "brief", batch_size=3)
        out = tr.translate(["a", "b", "c", "d"])
        self.assertEqual(out, ["Ta", "Tb", "Tc", "Td"])
        self.assertEqual(tr.failed_lines, 0)


class _Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.server.reject:
            body = json.dumps({"error": {"code": "data_inspection_failed",
                                         "message": "Input data may contain inappropriate content."}}).encode()
            self.send_response(400)
        else:
            body = json.dumps({"choices": [{"message": {"content": "本地结果"}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _server(reject):
    s = ThreadingHTTPServer(("127.0.0.1", 0), _Mock)
    s.reject = reject
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


class Fallback(unittest.TestCase):
    def test_moderation_goes_to_fallback(self):
        cloud, local = _server(True), _server(False)
        try:
            fb = ChatClient(Endpoint(name="local", base_url=f"http://127.0.0.1:{local.server_port}"), "m")
            c = ChatClient(Endpoint(name="cloud", base_url=f"http://127.0.0.1:{cloud.server_port}/v1",
                                    thinking="enable_thinking"), "m", fallback=fb, usage=fb.usage)
            self.assertEqual(c.complete([{"role": "user", "content": "x"}]), "本地结果")
            self.assertEqual(c.usage.fallbacks, 1)
        finally:
            cloud.shutdown(); local.shutdown()


if __name__ == "__main__":
    unittest.main()
