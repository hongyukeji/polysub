"""Translation quality aids (Q1): lenient parsing, output checks, glossary,
following-line context, continuation marks, re-translation of flagged lines."""
import json
import threading
import unittest

from polysub.asr import Cue
from polysub.brief import make_glossary
from polysub.config import Endpoint
from polysub.translate import CONT, Translator, _parse, continuation_marks, problems


class ScriptedClient:
    """Answers each request with fn(lines, message) and records the messages."""

    def __init__(self, fn):
        self.fn = fn
        self.ep = Endpoint(name="fake", concurrency=1)
        self.cancel = threading.Event()
        self.msgs = []

    def complete(self, messages, **kw):
        user = messages[-1]["content"]
        self.msgs.append((messages[0]["content"], user))
        body = user.split("Translate these lines:\n", 1)[1]
        lines = json.loads(body[:body.index("}") + 1])
        return json.dumps({k: self.fn(v, user) for k, v in lines.items()}, ensure_ascii=False)


class Parse(unittest.TestCase):
    def test_broken_json_keeps_good_items(self):
        text = '{"1": "早上好", "2": "你好\\"呀\\"", "3": "晚安",}'   # trailing comma
        self.assertEqual(_parse(text, 3), {1: "早上好", 2: '你好"呀"', 3: "晚安"})
        self.assertEqual(_parse('{"1": "好", "2": "还没', 2), {1: "好"})     # cut off

    def test_strips_continuation_mark(self):
        self.assertEqual(_parse('{"1": "我觉得 ⤵"}', 1), {1: "我觉得"})


class Checks(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(problems("部長はどこ", "部长在哪里", "zh-Hans", "ja"), [])
        self.assertIn("source script left in the translation", problems("まだです", "还まだ", "zh-Hans", "ja"))
        self.assertTrue(problems("今日は会議です", "今天有 meeting", "zh-Hans", "ja")[0].startswith("contains foreign"))
        self.assertEqual(problems("PolySub を使う", "用 PolySub", "zh-Hans", "ja"), [])   # word from the source
        self.assertIn("not translated", problems("Good morning", "Good morning", "zh-Hans", "en"))
        short = problems("田中さんは明日の会議に来ないと言っていました", "好", "zh-Hans", "ja")
        self.assertTrue(any("shorter" in w for w in short))
        g = problems("田中さん、おはよう", "多中，早", "zh-Hans", "ja", {"田中": "田中"})
        self.assertTrue(any(w.startswith("glossary") for w in g))

    def test_japanese_target_allows_kana(self):
        self.assertEqual(problems("Good morning, Tanaka", "おはよう、田中さん", "ja", "en"), [])


class Context(unittest.TestCase):
    def test_following_lines_and_glossary_only_when_used(self):
        c = ScriptedClient(lambda s, _: "T" + s)
        tr = Translator(c, "ja", "zh-Hans", batch_size=2, lookahead_lines=2,
                        glossary={"田中": "田中", "部長": "部长", "課長": "科长"})
        out = tr.translate(["田中です", "よろしく", "部長は", "まだ"])
        self.assertEqual(out, ["T田中です", "Tよろしく", "T部長は", "Tまだ"])
        first = c.msgs[0][1]
        self.assertIn("Following lines (context only, do not translate):\n- 部長は\n- まだ", first)
        self.assertIn("- 田中 → 田中", first)
        self.assertIn("- 部長 → 部长", first)     # appears in the following lines
        self.assertNotIn("課長", first)
        self.assertNotIn("Following lines", c.msgs[1][1])   # nothing after the last batch

    def test_careful_rules_can_be_switched_off(self):
        c = ScriptedClient(lambda s, _: "T")
        Translator(c, "ja", "zh-Hans", careful=True).translate(["a"])
        Translator(c, "ja", "zh-Hans", careful=False).translate(["a"])
        self.assertIn("omit the subject", c.msgs[0][0])
        self.assertNotIn("omit the subject", c.msgs[1][0])

    def test_continuation_marks(self):
        cues = [Cue(0, 1, "明日の会議は"), Cue(1.2, 2, "十時からです。"), Cue(4, 5, "わかった"), Cue(5.1, 6, "はい")]
        self.assertEqual(continuation_marks(cues), [True, False, True, False])
        self.assertFalse(continuation_marks([Cue(0, 1, "明日は"), Cue(3, 4, "晴れ")])[0])   # long pause
        c = ScriptedClient(lambda s, _: "T" + s)
        out = Translator(c, "ja", "zh-Hans").translate([x.text for x in cues], continues=continuation_marks(cues))
        self.assertIn(f"明日の会議は {CONT}", c.msgs[0][1])
        self.assertEqual(out[0], "T明日の会議は")   # mark removed from the answer


class Recheck(unittest.TestCase):
    def test_flagged_line_translated_again(self):
        def fn(src, user):
            if "Note: a previous translation" in user:
                return "今天有会"
            return "今天有 meeting" if src.startswith("今日") else "好的"
        c = ScriptedClient(fn)
        tr = Translator(c, "ja", "zh-Hans", check=True)
        self.assertEqual(tr.translate(["今日は会議です", "はい"]), ["今天有会", "好的"])
        self.assertEqual((tr.flagged, tr.fixed), (1, 1))

    def test_worse_retry_is_not_taken(self):
        c = ScriptedClient(lambda s, u: "still meeting and party" if "Note:" in u else "有 meeting")
        tr = Translator(c, "ja", "zh-Hans", check=True)
        self.assertEqual(tr.translate(["会議"]), ["有 meeting"])
        self.assertEqual((tr.flagged, tr.fixed), (1, 0))

    def test_review_client_used_for_second_try(self):
        first = ScriptedClient(lambda s, u: "有 meeting")
        review = ScriptedClient(lambda s, u: "有会")
        tr = Translator(first, "ja", "zh-Hans", check=True, review_client=review)
        self.assertEqual(tr.translate(["会議"]), ["有会"])
        self.assertEqual(len(review.msgs), 1)


class Glossary(unittest.TestCase):
    def test_make_glossary(self):
        class C:
            def complete(self, messages, **kw):
                self.prompt = messages[0]["content"]
                return 'Here: {"田中": "田中", "部長": "部长", "x": "", "' + "長" * 30 + '": "y"}'
        c = C()
        self.assertEqual(make_glossary(c, "田中是部长", "田中、部長", "ja", "Simplified Chinese"),
                         {"田中": "田中", "部長": "部长"})
        self.assertIn("Simplified Chinese", c.prompt)
        self.assertEqual(make_glossary(c, "", "", "ja", "English"), {})


if __name__ == "__main__":
    unittest.main()


class PulledForward(unittest.TestCase):
    def test_duplicate_neighbour_is_translated_again(self):
        from polysub.translate import Translator, _overlap
        self.assertGreater(_overlap("已经能看到疫苗使感染者减少的效果", "在接种推进的国家，已经能看到疫苗使感染者减少的效果"), 0.5)
        self.assertEqual(_overlap("是的。", "是的。"), 0.0)          # short replies may repeat
        calls = []

        class C:
            cancel = __import__("threading").Event()

            def complete(self, msgs, max_tokens=0, json_mode=False, temperature=None):
                calls.append(msgs[-1]["content"])
                return '{"1": "我们接下来该怎么做呢"}'
        t = Translator(C(), "ja", "zh-Hans", check=True)
        out = t._fix_pulled_forward(["していくでしょうか。", "接種が進んでいる国では…"],
                                    ["在接种推进的国家已经能看到疫苗的效果", "在接种推进的国家，已经能看到疫苗使感染者减少的效果"])
        self.assertEqual(out[0], "我们接下来该怎么做呢")
        self.assertIn("only a fragment", calls[0])
