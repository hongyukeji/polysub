# Third-party notices

PolySub's own source code is MIT-licensed (see `LICENSE`). The packaged
`PolySub.app` also contains the components below, each under its own license.
They are dynamically loaded libraries or data files inside the bundle and can
be replaced; their source code is available at the links given.

| Component | License | Source |
| --- | --- | --- |
| Python 3.12 | PSF License | https://www.python.org/ |
| Qt 6 / PySide6 (QtCore, QtGui, QtWidgets) | LGPL-3.0 | https://code.qt.io/ |
| PyAV | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| FFmpeg libraries bundled with PyAV (libavcodec, libavformat, libavutil, libswresample, …) | LGPL-3.0-or-later | https://ffmpeg.org/download.html |
| Codec libraries bundled with PyAV: x264, x265 | GPL-2.0-or-later | https://code.videolan.org/videolan/x264 · https://bitbucket.org/multicoreware/x265_git |
| Codec libraries bundled with PyAV: dav1d, libvpx, libopus, libwebp | BSD-style | https://code.videolan.org/videolan/dav1d · https://chromium.googlesource.com/webm/libvpx · https://opus-codec.org · https://chromium.googlesource.com/webm/libwebp |
| Codec libraries bundled with PyAV: LAME | LGPL-2.0-or-later | https://lame.sourceforge.io |
| Codec libraries bundled with PyAV: opencore-amr | Apache-2.0 | https://sourceforge.net/projects/opencore-amr |
| Codec libraries bundled with PyAV: SVT-AV1 | BSD-3-Clause-Clear | https://gitlab.com/AOMediaCodec/SVT-AV1 |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| Silero VAD model (`polysub/assets/silero_vad_v6.onnx`) and VAD code adapted from faster-whisper | MIT | https://github.com/snakers4/silero-vad · https://github.com/SYSTRAN/faster-whisper |
| NumPy | BSD-3-Clause | https://numpy.org |
| Requests / urllib3 / idna / charset-normalizer | Apache-2.0 / MIT / BSD-3-Clause / MIT | https://github.com/psf/requests |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| pysubs2 | MIT | https://github.com/tkarabela/pysubs2 |
| opencc-python-reimplemented | Apache-2.0 | https://github.com/yichen0831/opencc-python |
| tomlkit | MIT | https://github.com/python-poetry/tomlkit |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| filelock | Unlicense | https://github.com/tox-dev/filelock |
| whisper.cpp (`whisper-server`, built-in speech recognition) and ggml | MIT | https://github.com/ggml-org/whisper.cpp |
| llama.cpp (`llama-server`, built-in translation) and ggml | MIT | https://github.com/ggml-org/llama.cpp |
| PyInstaller bootloader | GPL-2.0 with bootloader exception | https://github.com/pyinstaller/pyinstaller |

Models for the built-in engine are not part of the package; PolySub downloads
them on request from Hugging Face. The default ones are Qwen3-ASR 1.7B
(Apache-2.0, https://huggingface.co/ggml-org/Qwen3-ASR-1.7B-GGUF) and Qwen3 GGUF
translation models (Apache-2.0, https://huggingface.co/Qwen and
https://huggingface.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF); OpenAI Whisper
large-v3-turbo in whisper.cpp format (MIT, https://huggingface.co/ggerganov/whisper.cpp)
is available as an option.

PolySub only decodes audio; the x264 / x265 encoders are never called, they
are present because the PyAV binary wheel links them.
