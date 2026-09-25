"""Unit tests for scripts/bench (no model, no network). Example sentences are made up."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "bench"))

import annotate  # noqa: E402
import judge  # noqa: E402
import metrics  # noqa: E402
import runner  # noqa: E402
from polysub.translate import Translator  # noqa: E402


class Metrics(unittest.TestCase):
    def test_residual_source(self):
        self.assertTrue(metrics.residual_source("ja", "zh-Hans", "田中さん、会議です", "田中さん，开会了"))
        self.assertFalse(metrics.residual_source("ja", "zh-Hans", "部長、会議です", "部长，开会了"))
        self.assertTrue(metrics.residual_source("zh", "en", "部长来了", "The 部长 is here"))
        self.assertFalse(metrics.residual_source("zh", "en", "部长来了", "The manager is here"))
        # a lowercase source word copied into a Chinese translation of English
        self.assertTrue(metrics.residual_source("en", "zh-Hans", "The meeting is tomorrow", "meeting 是明天"))
        # capitalised names are kept on purpose
        self.assertFalse(metrics.residual_source("en", "zh-Hans", "Tanaka is late", "Tanaka 迟到了"))

    def test_other_language(self):
        self.assertTrue(metrics.other_language("ja", "zh-Hans", "会議は明日です", "会议是明天，sorry"))
        self.assertFalse(metrics.other_language("ja", "zh-Hans", "会議は明日です", "会议是明天，OK"))
        self.assertFalse(metrics.other_language("ja", "zh-Hans", "会議はCEOと", "和CEO开会"))
        self.assertTrue(metrics.other_language("en", "zh-Hans", "See you", "またね"))
        self.assertTrue(metrics.other_language("zh", "en", "再见", "See you, 안녕"))
        self.assertFalse(metrics.other_language("zh", "en", "再见", "See you"))

    def test_omission(self):
        src = ["田中部長は明日の会議に来ません", "部長、資料はもう準備しました", "明日の朝九時に集まりましょう",
               "田中さんは今日休みです。部長にはもう伝えました"]
        tr = ["田中部长明天不来开会", "部长，资料已经准备好了", "明天早上九点集合吧", "田中休息"]
        flags = metrics.omission_flags(src, tr)
        self.assertEqual(flags, [False, False, False, True])
        # short source lines are never flagged
        self.assertEqual(metrics.omission_flags(["はい", "田中部長は明日の会議に来ません"], ["", "田中部长明天不来开会"]),
                         [False, False])

    def test_line_flags_and_counts(self):
        src = ["部長、おはようございます", "田中さん、元気？"]
        tr = ["部长，早上好", "田中さん、元気？"]
        flags = metrics.line_flags("ja", "zh-Hans", src, tr)
        self.assertTrue(flags[1]["untranslated"] and flags[1]["residual"])
        self.assertEqual(metrics.count_flags(flags), {"residual": 1, "other_lang": 0, "omission": 0, "untranslated": 1})

    def test_align_and_chrf(self):
        lines = [{"start": 1.0, "end": 2.0}, {"start": 5.0, "end": 6.0}, {"start": 9.0, "end": 9.5}]
        ref = [(0.9, 2.1, "部长好"), (4.0, 5.5, "开会"), (5.5, 7.0, "明天")]
        self.assertEqual(metrics.align(lines, ref), ["部长好", "开会 / 明天", ""])
        self.assertEqual(metrics.chrf(["部长好"], ["部长好"]), 100.0)
        self.assertLess(metrics.chrf(["部长你好"], ["部长好"]), 100.0)
        self.assertIsNone(metrics.chrf(["x"], [""]))


class Sampling(unittest.TestCase):
    def test_reproducible(self):
        a = annotate.sample_lines(300, 60, seed=7)
        self.assertEqual(a, annotate.sample_lines(300, 60, seed=7))
        self.assertEqual(len(a), 60)
        self.assertEqual(len(set(a)), 60)
        self.assertEqual(a, sorted(a))
        self.assertTrue(all(1 <= x <= 300 for x in a))
        self.assertNotEqual(a, annotate.sample_lines(300, 60, seed=8))
        self.assertEqual(annotate.sample_lines(10, 60), list(range(1, 11)))
        # pinned values: a change here means old sheets no longer match their runs
        self.assertEqual(annotate.sample_lines(100, 5, seed=0), [6, 34, 50, 54, 98])

    def test_match_rows(self):
        lines = [{"start": 0.0, "end": 1.0, "src": "田中です"}, {"start": 1.2, "end": 2.0, "src": "部長です"},
                 {"start": 2.5, "end": 4.0, "src": "会議です"}]
        sample = [{"line": 2, "start": "1.2", "end": "2.0", "src": "部長です"},
                  {"line": 9, "start": "2.6", "end": "3.0", "src": "別の認識結果"}]
        self.assertEqual(annotate.match_rows(sample, lines), [2, 3])


class Sheets(unittest.TestCase):
    def _lines(self):
        return [{"start": i, "end": i + 1, "src": f"部長の話 {i}", "tr": f"部长的话 {i}"} for i in range(10)]

    def test_roundtrip_and_summary(self):
        rows = annotate.blank_rows("demo", self._lines(), [1, 2, 3, 4, 5])
        marks = [("正确", ""), ("小瑕疵", "literal"), ("错误", "意思相反"), ("error", "term;omission"), ("", "")]
        for r, (lab, cat) in zip(rows, marks):
            r["label"], r["category"] = lab, cat
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.csv")
            annotate.write_sheet(p, rows, annotate.legend(["demo"]))
            back = annotate.read_sheet(p)
        self.assertEqual([r["line"] for r in back], [1, 2, 3, 4, 5])
        self.assertEqual(back[2]["categories"], ["reversed"])
        self.assertEqual(back[3]["categories"], ["term", "omission"])
        s = annotate.summarize(back)
        self.assertEqual((s["labelled"], s["correct"], s["minor"], s["error"]), (4, 1, 1, 2))
        self.assertEqual(s["error_rate"], 0.5)
        self.assertEqual(s["by_category"]["reversed"]["errors"], 1)
        self.assertEqual(s["by_category"]["term"]["rate"], 0.25)
        self.assertEqual(s["by_category"]["omission"]["errors"], 0)   # only the main category counts
        self.assertIn("demo", annotate.format_summary(annotate.summarize_by_item(back)))

    def test_labels_and_categories(self):
        self.assertEqual(annotate.norm_label(" 错误 "), "error")
        self.assertEqual(annotate.norm_label("Minor"), "minor")
        self.assertEqual(annotate.norm_label(""), "")
        with self.assertRaises(ValueError):
            annotate.norm_label("maybe")
        self.assertEqual(annotate.norm_categories("主语、人称弄错（谁对谁做）"), ["subject"])
        self.assertEqual(annotate.norm_categories("其他意思错误"), ["other"])
        self.assertEqual(annotate.norm_categories("混入其他语言的词 / 残留原文"), ["foreign"])
        self.assertEqual(annotate.norm_categories("asr_garbage; 直译"), ["asr_garbage", "literal"])
        with self.assertRaises(ValueError):
            annotate.norm_categories("typo")

    def test_agreement(self):
        def rows(labels, cats=None):
            cats = cats or [""] * len(labels)
            return [{"item": "x", "line": i + 1, "label": l, "categories": [c] if c else []}
                    for i, (l, c) in enumerate(zip(labels, cats))]
        a = rows(["correct", "error", "error", "minor", "correct"], ["", "term", "reversed", "", ""])
        b = rows(["correct", "error", "error", "correct", ""], ["", "term", "subject", "", ""])
        ag = annotate.agreement(a, b)
        self.assertEqual(ag["pairs"], 4)
        self.assertEqual(ag["label_agreement"], 0.75)
        self.assertEqual(ag["error_vs_not_agreement"], 1.0)
        self.assertEqual(ag["both_error"], 2)
        self.assertEqual(ag["category_agreement"], 0.5)
        self.assertEqual(ag["confusion"]["minor"]["correct"], 1)
        self.assertEqual(annotate.agreement(a, [])["pairs"], 0)


class _Ep:
    concurrency = 1


class FakeClient:
    """Stands in for ChatClient: answers from a script of replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.ep = _Ep()
        self.cancel = threading.Event()
        self.usage = None

    def complete(self, messages, max_tokens=0, json_mode=False, temperature=None):
        return self.replies.pop(0)


class JsonStats(unittest.TestCase):
    def test_counts(self):
        replies = [
            '{"1": "田中来了", "2": "部长走了"}',        # batch 1: complete on the first try
            'sorry, I cannot',                          # batch 2: unparseable ...
            '{"1": "开会"}',                            # ... retry is partial ...
            '{"1": "明天"}',                            # ... line 2 alone succeeds
            '{"1": "好的"}',                            # batch 3 (one line): fine
        ]
        tr = Translator(FakeClient(replies), "ja", "zh-Hans", batch_size=2, context_lines=2)
        rec = runner.Recorder()
        with rec.active():
            out = tr.translate(["田中が来た", "部長が帰った", "会議", "明日", "はい"])
        self.assertEqual(out, ["田中来了", "部长走了", "开会", "明天", "好的"])
        st = rec.json_stats()
        self.assertEqual(st["batches"], 3)
        self.assertEqual(st["batch_first_fail"], 1)
        self.assertEqual(st["batch_first_unparseable"], 1)
        self.assertEqual(st["attempts"], 4)
        self.assertEqual(st["attempt_fail_rate"], 0.5)
        self.assertEqual(st["line_fallback_calls"], 1)
        self.assertEqual(st["line_fallback_ok"], 1)
        self.assertEqual(st["failed_lines"], 0)
        self.assertIn("translate", dict(rec.events))
        # instrumentation is removed afterwards
        self.assertEqual(Translator._ask.__name__, "_ask")
        self.assertNotIn("rec", Translator._ask.__code__.co_freevars)

    def test_failed_line_kept(self):
        tr = Translator(FakeClient(["{}", "{}", "no"]), "ja", "zh-Hans", batch_size=5)
        rec = runner.Recorder()
        with rec.active():
            out = tr.translate(["部長"])
        self.assertEqual(out, ["部長"])
        self.assertEqual(rec.json_stats()["failed_lines"], 1)


class JudgeParse(unittest.TestCase):
    def test_parse(self):
        text = '<think>x</think>```json\n{"3": {"label": "错误", "category": "subject", "reason": "主语反了"},' \
               ' "4": {"label": "correct", "category": "", "reason": ""}, "5": {"label": "??"}}\n```'
        got = judge._parse(text.split("</think>")[-1])
        self.assertEqual(got[3]["label"], "error")
        self.assertEqual(got[3]["categories"], ["subject"])
        self.assertEqual(got[4]["label"], "correct")
        self.assertNotIn(5, got)

    def test_grade_retries_missing(self):
        lines = [{"start": i, "end": i + 1, "src": f"部長 {i}", "tr": f"部长 {i}"} for i in range(1, 7)]
        c = FakeClient(['{"2": {"label": "正确"}}', '{"4": {"label": "错误", "category": "term"}}'])
        g = judge.Judge(c, "ja", "zh-Hans", batch=8).grade(lines, [2, 4])
        self.assertEqual(sorted(g), [2, 4])
        rows = judge.to_rows("x", lines, [2, 4], g, "llm:test")
        self.assertEqual([r["label"] for r in rows], ["正确", "错误"])


class Config(unittest.TestCase):
    def test_overlay_and_set(self):
        from polysub import config
        with tempfile.TemporaryDirectory() as d:
            os.environ["POLYSUB_CONFIG"] = os.path.join(d, "config.toml")
            try:
                ov = os.path.join(d, "fast.toml")
                with open(ov, "w") as f:
                    f.write('[translate]\nthink = "off"\nbatch_size = 40\n')
                cfg = runner.load_config(ov, ["translate.context_lines=10", "asr.two_pass=false"])
            finally:
                del os.environ["POLYSUB_CONFIG"]
        self.assertEqual((cfg.translate.think, cfg.translate.batch_size, cfg.translate.context_lines),
                         ("off", 40, 10))
        self.assertIs(cfg.asr.two_pass, False)
        d = runner.describe_config(cfg)
        self.assertNotIn("api_key", str(d))
        self.assertNotIn("http", str(d))
        with self.assertRaises(KeyError):
            runner.load_config("", ["translate.nope=1"])
        self.assertIsInstance(config.Config(), config.Config)


if __name__ == "__main__":
    unittest.main()
