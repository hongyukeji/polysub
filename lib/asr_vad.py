"""Silero VAD -> per-utterance ASR via oMLX /v1/audio/transcriptions -> SRT.

usage: asr_vad.py in.wav out.srt [--model NAME] [--lang ja|auto] [--offset SEC] [--prompt TERMS]
oMLX endpoint and key come from POLYSUB_OMLX / POLYSUB_OMLX_KEY.
"""
import argparse, io, json, os, re, sys, time

import numpy as np
import requests
import soundfile as sf
from faster_whisper.vad import VadOptions, get_speech_timestamps

SR = 16000
API = os.environ.get("POLYSUB_OMLX", "http://127.0.0.1:8888") + "/v1/audio/transcriptions"
KEY = os.environ.get("POLYSUB_OMLX_KEY", "")

# lines made only of kana interjections / breaths / punctuation
INTERJ = re.compile(r"^[\sあぁいぃうぅえぇおぉんっッーはハふフへヘほホアァイィウゥエェオォン、。…！!？?～〜・]*$")


def ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav"); ap.add_argument("out")
    ap.add_argument("--model", default="Qwen3-ASR-1.7B-8bit")
    ap.add_argument("--lang", default="ja")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--max-speech", type=float, default=12.0)
    ap.add_argument("--keep-interj", action="store_true")
    ap.add_argument("--prompt", default="")
    a = ap.parse_args()

    audio, sr = sf.read(a.wav, dtype="float32")
    assert sr == SR, sr
    t0 = time.time()
    opts = VadOptions(threshold=a.threshold, min_speech_duration_ms=300,
                      min_silence_duration_ms=400, speech_pad_ms=200,
                      max_speech_duration_s=a.max_speech)
    chunks = get_speech_timestamps(audio, opts)
    t_vad = time.time() - t0

    sess = requests.Session()
    rows, dropped, t_asr = [], 0, 0.0
    for c in chunks:
        seg = audio[c["start"]:c["end"]]
        buf = io.BytesIO(); sf.write(buf, seg, SR, format="WAV", subtype="PCM_16")
        t1 = time.time()
        r = sess.post(API, headers={"Authorization": f"Bearer {KEY}"},
                      files={"file": ("a.wav", buf.getvalue(), "audio/wav")},
                      data={"model": a.model, **({"language": a.lang} if a.lang != "auto" else {}), **({"prompt": a.prompt} if a.prompt else {})}, timeout=600)
        t_asr += time.time() - t1
        r.raise_for_status()
        text = (r.json().get("text") or "").strip()
        if not text:
            continue
        if a.prompt:
            residue = text
            for t in re.split(r"[、,，\s]+", a.prompt):
                if t:
                    residue = residue.replace(t, "")
            if len(re.sub(r"[\s、。，,？?！!…・]", "", residue)) <= 1:
                dropped += 1  # model echoed the biasing prompt
                continue
        if not a.keep_interj and INTERJ.match(text):
            dropped += 1
            continue
        rows.append((c["start"] / SR + a.offset, c["end"] / SR + a.offset, text))

    with open(a.out, "w") as f:
        for i, (s, e, t) in enumerate(rows, 1):
            f.write(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n\n")
    dur = len(audio) / SR
    speech = sum(c["end"] - c["start"] for c in chunks) / SR
    print(json.dumps({"audio_s": round(dur, 1), "speech_s": round(speech, 1),
                      "vad_chunks": len(chunks), "lines": len(rows),
                      "dropped_interjection": dropped, "vad_s": round(t_vad, 1),
                      "asr_s": round(t_asr, 1), "rtf": round((t_vad + t_asr) / dur, 3)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
