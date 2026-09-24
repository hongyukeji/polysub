"""Read a whole ASR transcript once and write a short "translation brief"
(setting, characters, recurring terms, likely ASR mishearings) that is passed
to VideoCaptioner as --prompt, plus 3-6 names/titles for the second ASR pass.

usage: context_note.py in.srt out.txt TARGET_LANG
  API base comes from POLYSUB_NOTE_API (a PolySub relay, which supplies the
  real model, key and thinking level). Writes out.txt and out.txt.terms.
"""
import os, re, sys, time

import requests

API = os.environ.get("POLYSUB_NOTE_API", "http://127.0.0.1:8888") + "/v1/chat/completions"

# Per-target style rules appended to the brief. Anything not listed uses the
# generic English rule with the language name filled in.
STYLE = {
    "zh-Hans": "翻译要求：使用简体中文和中国大陆常用说法；口语化、自然，保留语气词；"
               "忠实翻译，不删减、不淡化露骨内容；日文汉字的人名、称呼一律写成简体字（例如 宮→宫、機→机）。",
    "zh-Hant": "翻譯要求：使用繁體中文和台灣常用說法；口語化、自然，保留語氣詞；忠實翻譯，不刪減、不淡化露骨內容。",
    "en": "Translation rules: natural, colloquial English subtitles; keep the speaker's tone and "
          "interjections; translate faithfully without softening or omitting explicit content; "
          "romanize Japanese names in Hepburn (e.g. Miyashita) and keep honorifics only when meaningful.",
}
LANG_NAMES = {"ja": "Japanese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish",
              "ru": "Russian", "pt": "Portuguese", "pt-BR": "Brazilian Portuguese", "it": "Italian",
              "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "ar": "Arabic", "yue": "Cantonese"}
GENERIC = ("Translation rules: natural, colloquial {lang} subtitles; keep the speaker's tone; "
           "translate faithfully without softening or omitting explicit content; "
           "use the usual {lang} spelling for personal names.")

ASK = """下面是一部影片的语音识别（ASR）字幕，可能有同音误识别。请通读后，用简体中文写一份不超过 250 字的「翻译参考」，只包含：
1. 场景与人物关系（一两句）；
2. 人物及其称呼；
3. 反复出现的术语；
4. ASR 同音误识别：逐行检查放在句中不合语境的词（尤其是出现在称呼、人名、专有名词位置上的普通词。同音词很多，要按读音找出语境里真正该用的词；同一个称呼被识别成多种写法时要逐一列出），格式「误→正」，只列有把握的。
最后单独一行输出「ASR_TERMS: 」加上 3～6 个该片反复出现的人名和对人的称呼（例如姓氏、职务称呼；按原语言写法，用顿号分隔；不要普通名词、动词或身体部位词），供语音识别参考。
不要翻译字幕，不要任何其他内容。

字幕：
{text}"""


def clean_terms(raw):
    """Keep only short name-like items; drop anything that reads like a sentence
    (e.g. leaked reasoning), so the second ASR pass never gets garbage hints."""
    out = []
    for t in re.split(r"[、,，/;；]\s*", raw):
        t = t.strip(" 　。.「」\"'")
        if 0 < len(t) <= 12 and not re.search(r"[\s.?!？！:：]", t) and t not in out:
            out.append(t)
    return "、".join(out[:6])


def main():
    src, out, target = sys.argv[1], sys.argv[2], sys.argv[3]
    lines = [b.splitlines()[2] for b in re.split(r"\n\s*\n", open(src, encoding="utf-8").read().strip())
             if len(b.splitlines()) >= 3]
    t = time.time()
    r = requests.post(API, headers={"Authorization": "Bearer polysub"}, json={
        "model": "polysub",  # the relay substitutes the real model
        "messages": [{"role": "user", "content": ASK.format(text="\n".join(lines))}],
        "max_tokens": 16384}, timeout=3600)
    r.raise_for_status()
    note = r.json()["choices"][0]["message"]["content"].strip()
    note = re.sub(r"(?s)<think>.*?</think>", "", note).strip()
    m = re.findall(r"ASR_TERMS[:：]\s*(.+)", note)
    terms = clean_terms(m[-1]) if m else ""
    note = re.sub(r"\n?.*ASR_TERMS.*", "", note).strip()
    style = STYLE.get(target) or GENERIC.format(lang=LANG_NAMES.get(target, target))
    open(out, "w", encoding="utf-8").write(note + "\n\n" + style + "\n")
    open(out + ".terms", "w", encoding="utf-8").write(terms)
    print(f"{time.time() - t:.1f}s, {len(lines)} lines in; terms: {terms}")


if __name__ == "__main__":
    main()
