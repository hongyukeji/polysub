"""PolySub command line.

  polysub VIDEO... [-t zh-Hans,en] [-f auto] [--think low] ...   make subtitles now
  polysub queue add PATH... [-t ...]    add to the background queue and start it
  polysub queue list | run | clear | retry ID
  polysub config path | show
  polysub endpoints [test NAME]
  polysub install [--dest DIR]          copy PolySub.app to /Applications
  polysub doctor
"""
import argparse
import os
import sys
import time

from . import __version__, jobs, pipeline
from .api import ApiError, AsrClient, ChatClient
from .config import PRESETS, THINK_LEVELS, config_path, load, mask_key, with_overrides

SUBCOMMANDS = {"run", "queue", "config", "endpoints", "doctor", "gui", "install"}


def _langs(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def _overrides(a):
    return dict(general__source_lang=a.source, general__target_langs=_langs(a.to),
                general__output_format=a.format, general__bilingual=True if a.bilingual else None,
                general__on_exists="overwrite" if a.overwrite else None,
                translate__think=a.think, translate__think_budget=a.think_budget, translate__endpoint=a.translate_endpoint,
                translate__model=a.translate_model, translate__fallback_endpoint=a.fallback_endpoint,
                asr__endpoint=a.asr_endpoint, asr__model=a.asr_model)


def _add_common(p):
    p.add_argument("-t", "--to", help="字幕语言，逗号分隔，如 zh-Hans,en（默认取配置）")
    p.add_argument("-f", "--from", dest="source", help="视频原语言，auto = 自动识别（默认取配置）")
    p.add_argument("--think", choices=THINK_LEVELS, help="翻译思考档位：off 快速 | low 标准 | medium 精细")
    p.add_argument("--think-budget", type=int, help="每批翻译最多思考多少 token（0 = 不限）")
    p.add_argument("--translate-endpoint", help="翻译接口名称（config 里的 endpoints.name）")
    p.add_argument("--translate-model", help="翻译模型")
    p.add_argument("--fallback-endpoint", help="内容被拒时改用的接口名称")
    p.add_argument("--asr-endpoint", help="语音识别接口名称")
    p.add_argument("--asr-model", help="语音识别模型")
    p.add_argument("--format", choices=("srt", "ass", "vtt"))
    p.add_argument("--bilingual", action="store_true", help="双语字幕（译文 + 原文）")
    p.add_argument("--overwrite", action="store_true", help="已有同名字幕时覆盖")


def cmd_run(a):
    cfg = with_overrides(load(), **_overrides(a))
    code = 0
    for v in a.videos:
        last = [None]

        def show(p, name=os.path.basename(v)):
            if a.quiet:
                return
            key = (p.stage, p.lang)
            tail = f" {p.done}/{p.total}" if p.total else ""
            lang = f"[{p.lang}] " if p.lang else ""
            if key != last[0] or p.done == p.total:
                print(f"\r[{time.strftime('%H:%M:%S')}] {lang}{p.message}{tail}".ljust(70),
                      end="\n" if key != last[0] and last[0] else "", flush=True)
            else:
                print(f"\r[{time.strftime('%H:%M:%S')}] {lang}{p.message}{tail}".ljust(70), end="", flush=True)
            last[0] = key

        print(f"PolySub {__version__}: {os.path.basename(v)}")
        t0 = time.time()
        try:
            r = pipeline.run(v, cfg, progress=show, use_cache=not a.no_cache, output=a.output or "")
        except (ApiError, Exception) as e:
            print(f"\n失败：{e}", file=sys.stderr)
            code = 1
            continue
        print()
        for lang, path in r.outputs.items():
            print(f"  {lang}: {path}")
        for lang in r.skipped:
            print(f"  {lang}: 已有字幕，跳过（--overwrite 可覆盖）")
        for n in r.notes:
            print(f"  注：{n}")
        if r.seconds:
            print("  耗时：" + "，".join(f"{k} {v}s" for k, v in r.seconds.items()) + f"；合计 {time.time() - t0:.0f}s")
        if r.usage:
            print(f"  用量：{r.usage}")
    return code


def cmd_queue(a):
    if a.action == "add":
        cfg = load()
        targets = _langs(a.to) or cfg.general.target_langs
        added = jobs.add(a.paths, targets)
        print(f"加入队列 {len(added)} 个（→ {','.join(targets)}）")
        if added:
            jobs.notify("PolySub", f"已加入队列 {len(added)} 个视频（→ {','.join(targets)}）")
        if not a.no_start:
            jobs.start_background()
    elif a.action == "run":
        if not jobs.work(notify_user=True):
            if not getattr(a, "quiet", False):
                print("已有后台进程在处理队列")
    elif a.action == "list":
        for j in jobs.list_jobs():
            print(f"{j.id}  {j.status:9}  {','.join(j.targets):12}  {os.path.basename(j.video)}  {j.error[:60]}")
    elif a.action == "clear":
        jobs.clear()
    elif a.action == "retry":
        for i in a.paths:
            jobs.retry(i)
        jobs.start_background()
    return 0


def cmd_config(a):
    if a.action == "path":
        print(config_path())
    else:
        cfg = load()
        print(f"# {cfg.path}")
        for sec in ("general", "asr", "translate"):
            print(f"[{sec}]", vars(getattr(cfg, sec)))
        for e in cfg.endpoints:
            print(f"- {e.name}: {e.base_url}  key={mask_key(e.api_key)}  thinking={e.thinking}  并发={e.concurrency}")
    return 0


def cmd_endpoints(a):
    cfg = load()
    if a.action != "test":
        for e in cfg.endpoints:
            print(f"{e.name:20} {PRESETS.get(e.preset, {}).get('label', e.preset):12} {e.base_url}  key={mask_key(e.api_key)}")
        return 0
    e = cfg.endpoint(a.name)
    c = ChatClient(e, a.model or cfg.translate.model, "off")
    try:
        models = c.list_models()
        print(f"连接正常，{len(models)} 个模型：{', '.join(models[:12])}{' …' if len(models) > 12 else ''}")
    except Exception as ex:
        print(f"获取模型列表失败：{ex}")
    if a.model or e.name == cfg.translate.endpoint:
        try:
            out = c.complete([{"role": "user", "content": "把「今日もよろしく」翻译成简体中文，只输出译文"}], max_tokens=64)
            print(f"翻译测试（{c.model}）：{out}")
        except Exception as ex:
            print(f"翻译测试失败：{ex}")
            return 1
    return 0


def cmd_doctor(a):
    cfg = load()
    ok = True
    print(f"配置：{cfg.path}")
    try:
        import av
        print(f"✓ 音频解码 PyAV {av.__version__}")
    except Exception as e:
        ok = False; print(f"✗ PyAV：{e}")
    from .media import find_ffmpeg
    ff = find_ffmpeg(cfg.general.ffmpeg_path)
    print(f"{'✓' if ff else '·'} 系统 ffmpeg（仅作兜底）：{ff or '未找到，一般不需要'}")
    for role, epname, model in (("语音识别", cfg.asr.endpoint, cfg.asr.model),
                                ("翻译", cfg.translate.endpoint, cfg.translate.model)):
        try:
            e = cfg.endpoint(epname)
            models = ChatClient(e, model).list_models()
            have = model in models
            ok &= have
            print(f"{'✓' if have else '✗'} {role}：{e.name}（{e.base_url}）模型 {model}{'' if have else ' 不在模型列表里'}")
        except Exception as ex:
            ok = False
            print(f"✗ {role}：{epname} 无法连接：{ex}")
    return 0 if ok else 1


def _bundle_path():
    """Path of the running PolySub.app (packaged build), else ''."""
    exe = os.path.realpath(sys.executable)
    marker = ".app/Contents/MacOS/"
    return exe[: exe.index(marker) + 4] if getattr(sys, "frozen", False) and marker in exe else ""


def cmd_install(a):
    import shutil
    import subprocess
    src = _bundle_path()
    if not src:
        print("只有从打包好的 PolySub.app（例如 Homebrew 安装的）运行时才能用这个命令。", file=sys.stderr)
        return 2
    dest = os.path.join(os.path.expanduser(a.dest), "PolySub.app")
    if os.path.realpath(src) == os.path.realpath(dest):
        print(f"已经在 {dest}")
        return 0
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        # ditto keeps the signature and extended attributes; copying adds no quarantine flag
        subprocess.run(["ditto", src, dest], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"复制失败：{e}\n没有写入权限时可以改装到自己的应用程序目录：polysub install --dest ~/Applications",
              file=sys.stderr)
        return 1
    print(f"已安装到 {dest}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "run")
    elif argv and argv[0] in ("-t", "--to", "-f", "--from", "--think"):
        argv.insert(0, "run")
    ap = argparse.ArgumentParser(prog="polysub", description="视频 → 任意语言字幕")
    ap.add_argument("--version", action="version", version=f"PolySub {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="立即为视频生成字幕（默认命令）")
    p.add_argument("videos", nargs="+")
    _add_common(p)
    p.add_argument("-o", "--output", help="输出文件（只有一个视频、一种语言时）")
    p.add_argument("--no-cache", action="store_true", help="不用缓存的识别结果")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("queue", help="后台队列")
    qs = p.add_subparsers(dest="action", required=True)
    q = qs.add_parser("add", help="加入队列并在后台开始处理")
    q.add_argument("paths", nargs="+")
    q.add_argument("-t", "--to", help="字幕语言，逗号分隔（默认取配置）")
    q.add_argument("--no-start", action="store_true", help="只加入，不启动后台处理")
    q = qs.add_parser("run", help="在前台处理队列")
    q.add_argument("-q", "--quiet", action="store_true")
    qs.add_parser("list", help="查看队列")
    qs.add_parser("clear", help="清除已结束的任务")
    q = qs.add_parser("retry", help="重试失败的任务")
    q.add_argument("paths", nargs="+", metavar="ID")
    p.set_defaults(fn=cmd_queue)

    p = sub.add_parser("config", help="配置文件")
    p.add_argument("action", choices=("path", "show"), nargs="?", default="show")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("endpoints", help="接口配置")
    p.add_argument("action", choices=("list", "test"), nargs="?", default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--model")
    p.set_defaults(fn=cmd_endpoints)

    p = sub.add_parser("gui", help="打开图形界面")
    p.add_argument("paths", nargs="*", help="启动时加入队列的视频或文件夹")
    p.set_defaults(fn=lambda a: __import__("polysub.gui.app", fromlist=["main"]).main(["polysub"] + a.paths))

    p = sub.add_parser("install", help="把 PolySub.app 复制到「应用程序」（Homebrew 安装后用）")
    p.add_argument("--dest", default="/Applications", help="目标目录（默认 /Applications）")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("doctor", help="环境检查")
    p.set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
