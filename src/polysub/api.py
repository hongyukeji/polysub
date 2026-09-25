"""Clients for OpenAI-compatible endpoints: chat (translation) and
audio transcriptions (ASR). Handles each vendor's thinking switch, retries,
rate limits, content-moderation rejections and an optional fallback endpoint.
"""
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

from .config import Endpoint

THINK_BUDGET = {"low": 2048, "medium": 8192}
_MODERATION = re.compile(r"inspection|inappropriate|content[_ ]?(risk|filter|policy)|risk|sensitive|moderation|"
                         r"safety|违规|敏感|审核", re.I)


class Cancelled(Exception):
    pass


class ApiError(RuntimeError):
    """Non-recoverable: bad key, no balance, wrong URL/model..."""


class ModerationError(RuntimeError):
    """The provider refused the content."""


@dataclass
class Usage:
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    tokens: Dict[str, List[int]] = field(default_factory=dict)   # endpoint -> [in, out, calls]
    fallbacks: int = 0

    def add(self, name, inp, out):
        with self.lock:
            t = self.tokens.setdefault(name, [0, 0, 0])
            t[0] += inp; t[1] += out; t[2] += 1

    def summary(self) -> str:
        parts = [f"{n}: 输入 {v[0]:,} / 输出 {v[1]:,} token（{v[2]} 次）" for n, v in self.tokens.items()]
        if self.fallbacks:
            parts.append(f"{self.fallbacks} 批被拒后改用备用接口")
        return "；".join(parts)


def _headers(ep: Endpoint) -> dict:
    h = {"Content-Type": "application/json"}
    if ep.api_key:
        h["Authorization"] = f"Bearer {ep.api_key}"
    return h


def apply_thinking(body: dict, style: str, think: str, budget: int = 0) -> dict:
    on = think != "off"
    if style == "chat_template_kwargs":
        kw = {"enable_thinking": on}
        if on:
            kw["reasoning_effort"] = think
            if budget:
                body["thinking_budget"] = budget   # oMLX / vLLM request-level cap
        body["chat_template_kwargs"] = kw
    elif style == "enable_thinking":
        body["enable_thinking"] = on
        if on:
            body["thinking_budget"] = budget or THINK_BUDGET.get(think, 8192)
    elif style == "deepseek":
        body["thinking"] = {"type": "enabled" if on else "disabled"}
    elif style == "reasoning_effort" and on:
        body["reasoning_effort"] = think
    return body


def _strip_think(text: str) -> str:
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    return text.split("</think>")[-1].strip()


class ChatClient:
    def __init__(self, ep: Endpoint, model: str, think: str = "off", usage: Optional[Usage] = None,
                 cancel: Optional[threading.Event] = None, fallback: Optional["ChatClient"] = None,
                 think_budget: int = 0):
        self.ep, self.model, self.think, self.budget = ep, model, think, think_budget
        self.usage = usage or Usage()
        self.cancel = cancel or threading.Event()
        self.fallback = fallback
        self.session = requests.Session()

    def _post(self, body: dict) -> dict:
        """POST with retries. Streams the answer so a cancel can drop the connection
        (which also stops generation on the server) between chunks."""
        url = self.ep.root + "/v1/chat/completions"
        body = dict(body, stream=True, stream_options={"include_usage": True})
        delay = 2
        for attempt in range(6):
            if self.cancel.is_set():
                raise Cancelled()
            try:
                r = self.session.post(url, headers=_headers(self.ep), json=body, timeout=self.ep.timeout, stream=True)
            except requests.RequestException as e:
                if attempt >= 2:
                    raise ApiError(f"{self.ep.name} 连接失败：{e}") from e
                time.sleep(delay); delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                r.close()
                if attempt >= 5 or (r.status_code >= 500 and attempt >= 2):
                    raise ApiError(f"{self.ep.name} HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(delay); delay = min(delay * 2, 30)
                continue
            if r.status_code in (400, 403, 451) and _MODERATION.search(r.text):
                raise ModerationError(f"HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:
                raise ApiError(f"{self.ep.name} HTTP {r.status_code}: {r.text[:300]}")
            try:
                return self._read_stream(r)
            except requests.RequestException as e:  # connection dropped mid-stream
                if self.cancel.is_set():
                    raise Cancelled() from e
                if attempt >= 2:
                    raise ApiError(f"{self.ep.name} 连接中断：{e}") from e
                time.sleep(delay); delay *= 2
        raise ApiError(f"{self.ep.name} 重试次数用尽")

    def _read_stream(self, r) -> dict:
        if "text/event-stream" not in r.headers.get("Content-Type", ""):
            return r.json()  # server ignored stream=True
        parts, finish, usage = [], None, {}
        try:
            for line in r.iter_lines(decode_unicode=True):
                if self.cancel.is_set():
                    raise Cancelled()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if d.get("error"):
                    msg = json.dumps(d["error"], ensure_ascii=False)
                    raise ModerationError(msg) if _MODERATION.search(msg) else ApiError(f"{self.ep.name}: {msg[:300]}")
                usage = d.get("usage") or usage
                for ch in d.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        parts.append(delta["content"])
                    finish = ch.get("finish_reason") or finish
        finally:
            r.close()
        return {"choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish}], "usage": usage}

    def complete(self, messages: List[dict], max_tokens: int = 4096, json_mode: bool = False,
                 temperature: Optional[float] = None) -> str:
        try:
            return self._complete(messages, max_tokens, json_mode, temperature)
        except ModerationError:
            if not self.fallback:
                raise
            with self.usage.lock:
                self.usage.fallbacks += 1
            return self.fallback._complete(messages, max_tokens, json_mode, temperature)

    def _complete(self, messages, max_tokens, json_mode, temperature) -> str:
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode and (not self.ep.is_local or self.ep.preset == "builtin"):  # llama-server: grammar-constrained
            body["response_format"] = {"type": "json_object"}
        apply_thinking(body, self.ep.thinking, self.think, self.budget)
        d = self._post(body)
        u = d.get("usage") or {}
        self.usage.add(self.ep.name, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        ch = (d.get("choices") or [{}])[0]
        if ch.get("finish_reason") == "content_filter":
            raise ModerationError("finish_reason=content_filter")
        return _strip_think((ch.get("message") or {}).get("content") or "")

    def list_models(self) -> List[str]:
        r = self.session.get(self.ep.root + "/v1/models", headers=_headers(self.ep), timeout=30)
        if r.status_code >= 400:
            raise ApiError(f"HTTP {r.status_code}: {r.text[:200]}")
        return [m.get("id") for m in r.json().get("data", [])]


LANG_NAMES = {  # ASR language names returned by Qwen3-ASR / Whisper -> ISO 639-1
    "japanese": "ja", "chinese": "zh", "english": "en", "korean": "ko", "cantonese": "yue",
    "french": "fr", "german": "de", "spanish": "es", "russian": "ru", "portuguese": "pt",
    "italian": "it", "thai": "th", "vietnamese": "vi", "indonesian": "id", "arabic": "ar",
    "dutch": "nl", "turkish": "tr", "polish": "pl", "hindi": "hi", "malay": "ms",
}


def norm_lang(name: Optional[str]) -> str:
    if not name:
        return ""
    n = str(name).strip().lower()
    return LANG_NAMES.get(n, n if len(n) <= 3 else n)


class AsrClient:
    def __init__(self, ep: Endpoint, model: str, cancel: Optional[threading.Event] = None):
        self.ep, self.model = ep, model
        self.cancel = cancel or threading.Event()
        self.local = threading.local()

    def _session(self):
        if not hasattr(self.local, "s"):
            self.local.s = requests.Session()
        return self.local.s

    def transcribe(self, wav: bytes, language: str = "", prompt: str = "") -> Tuple[str, str]:
        """-> (text, detected language code)."""
        data = {"model": self.model}
        if language and language != "auto":
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        if self.ep.preset == "builtin":  # whisper-server reports the detected language only in verbose_json
            data["response_format"] = "verbose_json"
        h = {"Authorization": f"Bearer {self.ep.api_key}"} if self.ep.api_key else {}
        delay = 2
        for attempt in range(5):
            if self.cancel.is_set():
                raise Cancelled()
            try:
                r = self._session().post(self.ep.root + "/v1/audio/transcriptions", headers=h, data=data,
                                         files={"file": ("a.wav", wav, "audio/wav")}, timeout=self.ep.timeout)
            except requests.RequestException as e:
                if attempt >= 2:
                    raise ApiError(f"{self.ep.name} 连接失败：{e}") from e
                time.sleep(delay); delay *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt >= 4:
                    raise ApiError(f"{self.ep.name} HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(delay); delay = min(delay * 2, 30)
                continue
            if r.status_code >= 400:
                raise ApiError(f"{self.ep.name} HTTP {r.status_code}: {r.text[:300]}")
            d = r.json()
            return (d.get("text") or "").strip(), norm_lang(d.get("language"))
        raise ApiError(f"{self.ep.name} 重试次数用尽")
