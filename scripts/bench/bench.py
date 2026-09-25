"""PolySub translation-quality bench (Q0 of docs/plans/builtin-engine.md).

  uv run python scripts/bench/bench.py fetch                       download the public set (cache dir, sha256)
  uv run python scripts/bench/bench.py run --item all              translation-only run on the frozen transcripts
  uv run python scripts/bench/bench.py run --item all --asr video  full pipeline on the videos
  uv run python scripts/bench/bench.py run --video V --target zh-Hans [--reference R.srt]   any (private) video
  uv run python scripts/bench/bench.py run --asr-json asr.json --target zh-Hans              a cached recognition result
      config: user config.toml, then --config FILE (full config or partial overlay), then
      --set translate.think=off --set translate.batch_size=40 ...
  uv run python scripts/bench/bench.py sheet RUN/ITEM [-n 60] [--seed 0] [-o sheet.csv]   blank annotation sheet
  uv run python scripts/bench/bench.py judge RUN/ITEM [--sample sheet.csv] [--against human.csv]
  uv run python scripts/bench/bench.py summarize sheet.csv ... [--against other.csv ...]
  uv run python scripts/bench/bench.py freeze RUN/ITEM --item ID   store a run's transcript as data/ID/asr.json

Runs are written to <user cache>/polysub-bench/runs/<time>-<label>/<item>/ unless --out is given:
metrics.json, lines.json, compare.csv / compare.md (line-by-line with the reference), output subtitle.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import annotate  # noqa: E402
import fetch  # noqa: E402


def _progress(prefix):
    def show(d, n):
        print(f"\r  {prefix} {d}/{n}".ljust(40), end="" if d < n else "\n", flush=True)
    return show


# ---- fetch -----------------------------------------------------------------

def cmd_fetch(a):
    items = fetch.load_manifest()
    for it in items:
        if a.item and it["id"] not in a.item:
            continue
        print(f"{it['id']}: {it['title']} ({it['license']})")
        print(f"  -> {fetch.fetch(it)}")


# ---- run -------------------------------------------------------------------

def _jobs(a):
    """-> list of (name, kwargs) describing what to run."""
    import runner
    if a.video:
        return [(a.name or "video", dict(kind="video", video=a.video, target=a.target, reference=a.reference or ""))]
    if a.asr_json:
        return [(a.name or "asr", dict(kind="asr", asr=runner.load_asr(a.asr_json), target=a.target,
                                        source_lang=a.source_lang or "", reference=a.reference or ""))]
    items = fetch.load_manifest()
    ids = [it["id"] for it in items] if "all" in a.item else a.item
    jobs = []
    for iid in ids:
        it = fetch.item(iid)
        target = a.target or it["target"]
        ref = fetch.reference_path(it) if it.get("reference") else ""
        frozen = os.path.join(fetch.DATA, iid, "asr.json")
        mode = a.asr or ("repo" if os.path.exists(frozen) else "video")
        if mode == "repo":
            if not os.path.exists(frozen):
                sys.exit(f"{iid}: no frozen transcript at data/{iid}/asr.json; use --asr video")
            if ref and not os.path.exists(ref):
                fetch.download(it["reference"], ref, it.get("reference_sha256", ""))
            jobs.append((iid, dict(kind="asr", asr=runner.load_asr(frozen), target=target,
                                   source_lang=it["language"], reference=ref)))
        else:
            video = fetch.fetch(it)
            jobs.append((iid, dict(kind="video", video=video, target=target, reference=ref)))
    return jobs


def cmd_run(a):
    import runner
    if not (a.item or a.video or a.asr_json):
        sys.exit("give --item ID|all, --video PATH or --asr-json PATH")
    cfg = runner.load_config(a.config or "", a.set or [])
    target_default = cfg.general.target_langs[0]
    label = a.label or "run"
    root = a.out or os.path.join(fetch.cache_dir("runs"), time.strftime("%Y%m%d-%H%M%S") + "-" + label)
    os.makedirs(root, exist_ok=True)
    conf = runner.describe_config(cfg)
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"label": label, "sets": a.set or [], "config_file": bool(a.config), **conf}, f, ensure_ascii=False, indent=1)
    rows = []
    for name, job in _jobs(a):
        out = os.path.join(root, name)
        os.makedirs(out, exist_ok=True)
        target = job["target"] or target_default
        print(f"[{time.strftime('%H:%M:%S')}] {name} -> {target}", flush=True)
        if job["kind"] == "video":
            res = runner.run_video(job["video"], cfg, target, out, use_cache=not a.fresh)
        else:
            res = runner.run_asr_json(job["asr"], cfg, target, out, job.get("source_lang", ""), a.limit,
                                      use_cache=not a.fresh)
        ana = runner.analyse(res, job["reference"])
        row = runner.write_outputs(out, name, res, ana, {"label": label})
        rows.append(row)
        j = row["json"]
        print(f"  {row['lines']} lines, {row['wall_s']} s, translate tokens out {row['tokens']['translate']['out']:,}, "
              f"batch JSON first-attempt failures {j['batch_first_fail']}/{j['batches']}", flush=True)
    table = runner.summary_table(rows)
    t = cfg.translate
    head = (f"# {label}\n\n翻译：{t.endpoint} / {t.model}，思考 {t.think}（上限 {t.think_budget}），"
            f"每批 {t.batch_size} 行，上下文 {t.context_lines} 行；全片参考思考 {t.brief_think}；"
            f"识别：{cfg.asr.endpoint} / {cfg.asr.model}，两遍 {cfg.asr.two_pass}\n")
    with open(os.path.join(root, "summary.md"), "w", encoding="utf-8") as f:
        f.write(head + "\n" + table + "\n")
    with open(os.path.join(root, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("\n" + table)
    print(f"\n-> {root}")


# ---- sheets ----------------------------------------------------------------

def _load_run(d):
    with open(os.path.join(d, "lines.json"), encoding="utf-8") as f:
        return json.load(f)


def _picks(a, run):
    """Sample line numbers: from an existing sheet (mapped onto this run) or fresh."""
    lines = run["lines"]
    if getattr(a, "sample", None):
        sample = annotate.read_sheet(a.sample)
        return [p for p in annotate.match_rows(sample, lines) if p]
    return annotate.sample_lines(len(lines), a.n, a.seed)


def cmd_sheet(a):
    run = _load_run(a.run)
    picks = _picks(a, run)
    item = run.get("item") or os.path.basename(os.path.normpath(a.run))
    rows = annotate.blank_rows(item, run["lines"], picks, "human")
    out = a.o or os.path.join(a.run, "sheet.csv")
    src = f"sample from {os.path.basename(a.sample)}" if a.sample else f"n={a.n} seed={a.seed} of {len(run['lines'])} lines"
    annotate.write_sheet(out, rows, annotate.legend([f"item {item}: {run['source_lang']} -> {run['target']}, {src}"]))
    print(f"{len(rows)} rows -> {out}")


def _judge_client(a):
    import runner
    from polysub.api import ChatClient, Usage
    from polysub.config import Endpoint
    cfg = runner.load_config(a.config or "", [])
    if a.judge_base_url:
        ep = Endpoint(name="judge", base_url=a.judge_base_url, api_key=os.environ.get(a.judge_key_env or "", ""),
                      thinking=a.judge_thinking_style or "none")
    else:
        ep = cfg.endpoint(a.judge_endpoint or cfg.translate.endpoint)
    model = a.judge_model or cfg.translate.model
    usage = Usage()
    return ChatClient(ep, model, a.judge_think, usage, think_budget=a.judge_budget), usage, model


def cmd_judge(a):
    import judge
    run = _load_run(a.run)
    picks = _picks(a, run)
    client, usage, model = _judge_client(a)
    refs = None
    if a.with_reference:
        cmp_csv = os.path.join(a.run, "compare.csv")
        import csv
        with open(cmp_csv, encoding="utf-8-sig", newline="") as f:
            refs = [r["reference"] for r in csv.DictReader(f)]
    j = judge.Judge(client, run["source_lang"], run["target"], batch=a.batch)
    t0 = time.monotonic()
    grades = j.grade(run["lines"], picks, run.get("brief", "") if a.with_brief else "", refs, _progress("judge"))
    secs = time.monotonic() - t0
    item = run.get("item") or os.path.basename(os.path.normpath(a.run))
    annotator = f"llm:{model}/{a.judge_think}"
    rows = judge.to_rows(item, run["lines"], picks, grades, annotator)
    out = a.o or os.path.join(a.run, "judge.csv")
    annotate.write_sheet(out, rows, annotate.legend([
        f"item {item}: LLM-assisted labels ({annotator}), for reference only, not a human annotation",
        f"{len(grades)}/{len(picks)} lines graded in {secs:.0f} s; {usage.summary()}"]))
    summ = annotate.summarize_by_item(annotate.read_sheet(out))
    print(annotate.format_summary(summ))
    print(f"{len(grades)}/{len(picks)} graded in {secs:.0f} s -> {out}")
    if a.against:
        ag = annotate.agreement(annotate.read_sheet(a.against), annotate.read_sheet(out))
        print(annotate.format_agreement(ag, os.path.basename(a.against), "LLM"))


def cmd_summarize(a):
    rows = [r for p in a.sheets for r in annotate.read_sheet(p)]
    summ = annotate.summarize_by_item(rows)
    result = {"summary": summ}
    if a.against:
        other = [r for p in a.against for r in annotate.read_sheet(p)]
        result["agreement"] = annotate.agreement(rows, other)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    print(annotate.format_summary(summ))
    if a.against:
        print()
        print(annotate.format_agreement(result["agreement"], "sheets", "against"))


def cmd_freeze(a):
    src = os.path.join(a.run, "asr.json")
    with open(src, encoding="utf-8") as f:
        d = json.load(f)
    run = _load_run(a.run)
    cues = d.get("cues") or [{"start": l["start"], "end": l["end"], "text": l["src"]} for l in run["lines"]]
    out_dir = os.path.join(fetch.DATA, a.item)
    os.makedirs(out_dir, exist_ok=True)
    it = fetch.item(a.item)
    frozen = {"item": a.item, "license": it["license"], "source": it["source_page"],
              "note": "Recognition result of the baseline run, frozen for translation-only runs.",
              "lang": d.get("lang") or run["source_lang"], "terms": d.get("terms", ""),
              "cues": [{"start": round(c["start"], 3), "end": round(c["end"], 3), "text": c["text"]} for c in cues]}
    out = os.path.join(out_dir, "asr.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(frozen, f, ensure_ascii=False, indent=0)
        f.write("\n")
    print(f"{len(cues)} cues -> {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bench.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download the public set into the cache (sha256-checked)")
    f.add_argument("--item", action="append", help="only this item id (repeatable)")
    f.set_defaults(fn=cmd_fetch)

    r = sub.add_parser("run", help="run one configuration and collect metrics")
    r.add_argument("--item", action="append", help="public item id, or all (repeatable)")
    r.add_argument("--video", help="any video (e.g. a private one; the path is not stored in the repo)")
    r.add_argument("--asr-json", help="a recognition result: PolySub cache asr.json, editor sub-*.json, or data/*/asr.json")
    r.add_argument("--asr", choices=("repo", "video"), help="public items: frozen transcript (repo) or full pipeline (video)")
    r.add_argument("--name", help="item name in the output for --video / --asr-json")
    r.add_argument("--source-lang", help="source language for --asr-json when the file does not say")
    r.add_argument("--target", help="target language (default: item target / first configured)")
    r.add_argument("--reference", help="reference subtitle for the side-by-side table")
    r.add_argument("--config", help="PolySub config.toml, or a partial overlay with [translate] / [asr] keys")
    r.add_argument("--set", action="append", metavar="SECTION.KEY=VALUE", help="override, e.g. translate.think=off")
    r.add_argument("--limit", type=int, default=0, help="translate only the first N lines (--asr-json / repo)")
    r.add_argument("--fresh", action="store_true", help="ignore caches (recognition and brief)")
    r.add_argument("--label", help="run label (directory name suffix)")
    r.add_argument("--out", help="output directory (default: user cache polysub-bench/runs/...)")
    r.set_defaults(fn=cmd_run)

    def sample_args(x):
        x.add_argument("run", help="run item directory (has lines.json)")
        x.add_argument("-n", type=int, default=60, help="lines to sample (default 60)")
        x.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
        x.add_argument("--sample", help="reuse the lines of an existing sheet (mapped by text, else by time)")
        x.add_argument("-o", help="output CSV")

    s = sub.add_parser("sheet", help="blank annotation sheet for a run")
    sample_args(s)
    s.set_defaults(fn=cmd_sheet)

    j = sub.add_parser("judge", help="LLM-assisted labels for the sampled lines")
    sample_args(j)
    j.add_argument("--against", help="human (or draft) sheet to compute agreement with")
    j.add_argument("--config", help="PolySub config.toml to read endpoints from")
    j.add_argument("--judge-endpoint", help="endpoint name in config (default: translation endpoint)")
    j.add_argument("--judge-model", help="model (default: translation model)")
    j.add_argument("--judge-think", choices=("off", "low", "medium"), default="low")
    j.add_argument("--judge-budget", type=int, default=0, help="max thinking tokens per request (0 = no cap)")
    j.add_argument("--judge-base-url", help="any OpenAI-compatible URL instead of a configured endpoint")
    j.add_argument("--judge-key-env", help="environment variable holding the key for --judge-base-url")
    j.add_argument("--judge-thinking-style", help="thinking switch style for --judge-base-url (see config.py)")
    j.add_argument("--batch", type=int, default=8, help="lines graded per request")
    j.add_argument("--with-brief", action="store_true", help="give the judge the run's brief")
    j.add_argument("--with-reference", action="store_true", help="give the judge the aligned reference subtitle")
    j.set_defaults(fn=cmd_judge)

    m = sub.add_parser("summarize", help="error rates from annotated sheets (and agreement)")
    m.add_argument("sheets", nargs="+")
    m.add_argument("--against", nargs="+", help="second annotator's sheets")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_summarize)

    z = sub.add_parser("freeze", help="store a run's transcript as data/<item>/asr.json")
    z.add_argument("run")
    z.add_argument("--item", required=True)
    z.set_defaults(fn=cmd_freeze)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
