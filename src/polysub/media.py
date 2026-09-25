"""Load the audio track of any video/audio file as 16 kHz mono float32.

PyAV (bundled FFmpeg libraries) is used first; if it cannot open the file, a
system ffmpeg binary is tried (configured path, PATH, Homebrew locations).
"""
import os
import shutil
import subprocess

import numpy as np

SR = 16000
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".m4v", ".avi", ".wmv", ".flv", ".ts", ".m2ts",
              ".webm", ".mpg", ".mpeg", ".3gp", ".rmvb", ".rm", ".vob", ".ogv",
              ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}


class MediaError(RuntimeError):
    pass


def is_media(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def find_ffmpeg(configured: str = "") -> str:
    for c in (configured, shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg",
              "/usr/local/bin/ffmpeg", r"C:\ffmpeg\bin\ffmpeg.exe"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""


def _load_pyav(path: str) -> np.ndarray:
    import av

    with av.open(path) as c:
        if not c.streams.audio:
            raise MediaError("没有音轨")
        stream = c.streams.audio[0]
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        parts = []
        for frame in c.decode(stream):
            for out in rs.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in rs.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        raise MediaError("音轨为空")
    return np.concatenate(parts).astype(np.float32)


def _load_ffmpeg(path: str, ffmpeg: str) -> np.ndarray:
    r = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise MediaError(r.stderr.decode(errors="replace").strip()[-300:] or "ffmpeg 解码失败")
    return np.frombuffer(r.stdout, np.float32).copy()


def load_audio(path: str, ffmpeg_path: str = "") -> np.ndarray:
    try:
        return _load_pyav(path)
    except Exception as e:  # unusual container/codec: try a system ffmpeg
        ff = find_ffmpeg(ffmpeg_path)
        if not ff:
            raise MediaError(f"无法读取音频（{e}）。可以安装 ffmpeg 后重试，或在设置里填写 ffmpeg 路径。") from e
        return _load_ffmpeg(path, ff)
