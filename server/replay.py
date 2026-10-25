"""说说 · 重跑识别记录：把识别记录里存的音频用当前热词再识别一遍，列出结果变了的句子。

改热词前可以先用 --hotwords 指定候选表跑一遍，看修好了哪些、有没有改坏别的，满意了再替换 hotwords.txt。
会另外加载一份模型（约 5 GB 显存），识别服务开着也能跑。音频要服务端设了 SHUOSHUO_HISTORY_AUDIO=1 才有。

用法：.venv/bin/python replay.py [--hotwords 候选热词.txt] [--last 200] [history/2026-10.jsonl ...]
"""
import argparse
import difflib
import glob
import json
import logging
import os
import re
import sys
import time

from server import Hotwords, Recognizer, read_wav

import itn

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = re.compile(r"[A-Za-z0-9_.+#-]+|\s+|.")  # 英文单词、数字整个比，汉字和标点一个一个比


def default_model():
    """和 run_server.sh 一样：先看环境变量，再看 server/.env 里的 SHUOSHUO_MODEL"""
    model = os.environ.get("SHUOSHUO_MODEL")
    if not model:
        try:
            with open(os.path.join(HERE, ".env"), encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.partition("=")
                    if k.strip() == "SHUOSHUO_MODEL":
                        model = v.split("#")[0].strip().strip("'\"")
        except OSError:
            pass
    return model or "Qwen/Qwen3-ASR-1.7B"


def load(paths):
    """读识别记录，只留带音频的；音频路径是相对 .jsonl 所在目录的"""
    recs = []
    for path in paths:
        folder = os.path.dirname(os.path.abspath(path))
        with open(path, encoding="utf-8") as f:
            recs += [(folder, r) for r in map(json.loads, filter(str.strip, f)) if r.get("audio")]
    return recs


def diff(a, b):
    """只列改动的片段：'… IMDb Job 上…' vs '… IMDb JOB 上…' → 'Job → JOB'"""
    x, y = TOKEN.findall(a), TOKEN.findall(b)
    ops = difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes()
    return " | ".join(f"{''.join(x[i1:i2]).strip() or '∅'} → {''.join(y[j1:j2]).strip() or '∅'}"
                      for op, i1, i2, j1, j2 in ops if op != "equal")


def main():
    ap = argparse.ArgumentParser(description="说说 · 用当前热词重跑识别记录里的音频，列出结果变了的句子")
    ap.add_argument("files", nargs="*", help="要重跑的 .jsonl，默认 server/history/ 下全部")
    ap.add_argument("--hotwords", default=os.path.join(HERE, "hotwords.txt"), help="换成候选热词表，先试再替换")
    ap.add_argument("--last", type=int, default=0, help="只重跑最近 N 句")
    ap.add_argument("--model", default=default_model(), help="默认和 run_server.sh 一样取 SHUOSHUO_MODEL")
    ap.add_argument("--language", default="", help="和服务端保持一致，留空自动识别")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--no-itn", action="store_true", help="不转阿拉伯数字，和记录里转换前的原文比")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not os.path.exists(args.hotwords):
        ap.error(f"找不到热词表 {args.hotwords}")
    recs = load(args.files or sorted(glob.glob(os.path.join(HERE, "history", "*.jsonl"))))
    if args.last:
        recs = recs[-args.last:]
    if not recs:
        sys.exit("没有带音频的识别记录：服务端要设 SHUOSHUO_HISTORY=history 和 SHUOSHUO_HISTORY_AUDIO=1")

    context = Hotwords(args.hotwords).get()
    recognizer = Recognizer(args.model, args.device, args.language)
    progress = sys.stderr.isatty()
    changed = bad = 0
    t0 = time.perf_counter()
    for i, (folder, r) in enumerate(recs, 1):
        if progress:
            print(f"\r{i}/{len(recs)}", end="", file=sys.stderr, flush=True)
        try:
            with open(os.path.join(folder, r["audio"]), "rb") as f:
                wav, sr = read_wav(f.read())
        except Exception:  # 音频被删了或者坏了，跳过接着跑
            bad += 1
            continue
        _, text = recognizer(wav, sr, context)
        old = r.get("raw", r["text"]) if args.no_itn else r["text"]
        if not args.no_itn:
            text = itn.normalize(text)
        if text != old:
            changed += 1
            if progress:
                print("\r\033[K", end="", file=sys.stderr)
            print(f"{r['t']}  {r['audio']}\n  旧：{old}\n  新：{text}\n  改动：{diff(old, text)}\n", flush=True)
    if progress:
        print("\r\033[K", end="", file=sys.stderr)
    print(f"重跑 {len(recs) - bad} 句，{changed} 句结果变了" + (f"，{bad} 句音频读不出来" if bad else "")
          + f"，用时 {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
