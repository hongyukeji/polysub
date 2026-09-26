"""Unit tests (no model needed). Run: .venv/bin/python -m unittest discover -s tests"""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from polysub import asr, config, subtitle, translate
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
        body = messages[-1]["content"].split("Translate these lines:\n", 1)[1]
        lines = json.loads(body[:body.index("}") + 1])
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


class SpeedDefaults(unittest.TestCase):
    def test_new_config_is_fast(self):
        c = config.default_config()
        self.assertEqual((c.translate.think, c.translate.think_budget), ("off", 0))
        self.assertEqual(c.translate.batch_lines(), 1)    # in batches models shifted sentences between lines
        self.assertEqual(c.find_endpoint(config.BUILTIN).concurrency, 4)
        c.translate.think = "low"
        self.assertEqual(c.translate.batch_lines(), 20)
        self.assertFalse(c.translate.continuation_marks)
        self.assertAlmostEqual(c.translate.temperature, 0.3)
        c.translate.batch_size = 30
        self.assertEqual(c.translate.batch_lines(), 30)

    def test_existing_config_keeps_its_choice(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.toml")
            with open(p, "w", encoding="utf-8") as f:
                f.write('[translate]\nthink = "low"\nthink_budget = 1024\nbatch_size = 20\n'
                        '[asr]\ntwo_pass = true\n')
            c = config.load(p)
            self.assertEqual((c.translate.think, c.translate.think_budget, c.translate.batch_lines()), ("low", 1024, 20))
            self.assertEqual(c.asr.second_pass, "auto")


class SecondPass(unittest.TestCase):
    def test_mishearings(self):
        brief = "田中是部长。ASR 同音误识别：部長→部长、多中→田中，课长 → 課長"
        self.assertEqual(asr.mishearings(brief), ["部長", "多中", "课长"])

    def test_recheck_only_candidates(self):
        sr = asr.SR
        segs = [(0, sr), (2 * sr, 3 * sr), (4 * sr, 5 * sr), (6 * sr, 7 * sr)]
        cues = [asr.Cue(0.0, 1.0, "田中さん、おはよう"), asr.Cue(2.0, 3.0, "今日は晴れ"),
                asr.Cue(6.0, 7.0, "多中部長はどこ")]           # segment 2 had no text
        self.assertEqual(asr.recheck_segments(segs, cues, "田中、部長", "多中→田中"), [0, 3])
        self.assertEqual(asr.recheck_segments(segs, cues, "", ""), [])

    def test_merge_keeps_first_pass_when_dropped(self):
        first = [asr.Cue(0.0, 1.0, "a"), asr.Cue(2.0, 3.0, "b"), asr.Cue(6.0, 7.0, "c")]
        second = [asr.Cue(6.0, 7.0, "C")]
        self.assertEqual([c.text for c in asr.merge_cues(first, second)], ["a", "b", "C"])


class Batching(unittest.TestCase):
    def test_batch_size_sets_request_count(self):
        client = FakeClient()
        n = []
        tr = translate.Translator(client, "ja", "zh-Hans", batch_size=40)
        tr._batch = lambda lines, ctx, after=(): (n.append(len(lines)), list(lines))[1]
        tr.translate([str(i) for i in range(90)])
        self.assertEqual(n, [40, 40, 10])


class PipelineSecondPass(unittest.TestCase):
    """pipeline.run with recognition, brief and translation mocked out."""

    def _run(self, second_pass):
        from unittest import mock
        import numpy as np
        from polysub import pipeline
        sr = asr.SR
        segs = [(0, sr), (2 * sr, 3 * sr), (4 * sr, 5 * sr)]
        first = {0: "多中さん", 2 * sr: "今日は晴れ", 4 * sr: "部長、行きます"}
        calls = []

        def fake_transcribe(client, audio, sg, lang, prompt="", progress=None):
            calls.append((prompt, [s for s, _ in sg]))
            pre = "2:" if prompt else ""
            return [asr.Cue(s / sr, e / sr, pre + first[s]) for s, e in sg], {}

        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "v.mp4")
            open(video, "wb").close()
            cfg = config.default_config()
            cfg.general.source_lang = "ja"
            cfg.asr.second_pass = second_pass
            with mock.patch.object(pipeline, "load_audio", return_value=np.zeros(6 * sr, "float32")), \
                    mock.patch.object(pipeline, "speech_segments", return_value=segs), \
                    mock.patch.object(pipeline, "transcribe", side_effect=fake_transcribe), \
                    mock.patch.object(pipeline, "make_brief", return_value=("多中→田中", "田中、部長")), \
                    mock.patch.object(pipeline, "user_cache_dir", return_value=d), \
                    mock.patch.object(pipeline.Translator, "translate", lambda self, lines, progress=None, continues=None: lines), \
                    mock.patch.object(pipeline, "make_glossary", return_value={}), \
                    mock.patch.object(pipeline.builtin, "start") as start:
                res = pipeline.run(video, cfg, ["zh-Hans"])
                self.started = [c.args[1] for c in start.call_args_list]
            with open(res.outputs["zh-Hans"], encoding="utf-8") as f:
                return calls, f.read()

    def test_auto_rechecks_candidates_only(self):
        calls, srt = self._run("auto")
        self.assertEqual(calls[1], ("田中、部長", [0, 4 * asr.SR]))
        self.assertIn("2:多中さん", srt)
        self.assertIn("今日は晴れ", srt)
        self.assertNotIn("2:今日は晴れ", srt)

    def test_builtin_servers_started_before_their_steps(self):
        self._run("auto")
        self.assertEqual(self.started, ["asr", "mt", "asr", "mt"])   # asr1, brief, asr2, translate

    def test_all_rechecks_everything(self):
        calls, _ = self._run("all")
        self.assertEqual(len(calls[1][1]), 3)


class Hallucination(unittest.TestCase):
    def test_known_phrases_only(self):
        for t in ("ご視聴ありがとうございました。", "谢谢观看！", "Thanks for watching!", "字幕由Amara.org社区提供"):
            self.assertTrue(asr.is_hallucination(t), t)
        for t in ("田中さん、ありがとうございました", "谢谢你来看我", "Thanks for coming"):
            self.assertFalse(asr.is_hallucination(t), t)


class _Sse(BaseHTTPRequestHandler):
    """llama-server style stream: text/event-stream without a charset."""

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in ("你好，", "田中さん"):
            chunk = {"choices": [{"delta": {"content": piece}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


class StreamEncoding(unittest.TestCase):
    def test_utf8_without_charset(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sse)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ep = Endpoint(name="t", base_url=f"http://127.0.0.1:{srv.server_port}")
            out = ChatClient(ep, "m").complete([{"role": "user", "content": "x"}])
            self.assertEqual(out, "你好，田中さん")
        finally:
            srv.shutdown()


class HintEcho(unittest.TestCase):
    def test_second_pass_line_with_the_hint_list_keeps_the_first_pass(self):
        from polysub.asr import merge_cues
        terms = "田中さん、部長、会議、資料"
        first = [asr.Cue(0, 5, "田中さん、今日の会議の資料だよね。"), asr.Cue(5, 8, "ぶちょうに聞いて。")]
        second = [asr.Cue(0, 5, "田中さん、部長、会議、資料だよね。"), asr.Cue(5, 8, "部長に聞いて。")]
        out = merge_cues(first, second, terms)
        self.assertEqual(out[0].text, "田中さん、今日の会議の資料だよね。")   # echo rejected
        self.assertEqual(out[1].text, "部長に聞いて。")                       # real correction kept
