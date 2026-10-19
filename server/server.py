"""说说 · 识别服务：POST /asr 收一整段 WAV，返回 Qwen3-ASR 的识别文本。

客户端录完整句再上传，这里整段识别：网络差只会慢一点，不会把结果识别坏。
"""
import argparse
import hmac
import io
import json
import logging
import os
import sys
import threading
import time
import types
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
import transformers

transformers.logging.set_verbosity_error()  # 不然每条请求都打一行 pad_token_id 警告

# qwen_asr 导入时会连带导入 nagisa（只有时间戳对齐用得到，还会初始化 DyNet 占 512MB 内存），塞个空模块跳过
sys.modules.setdefault("nagisa", types.ModuleType("nagisa"))
from qwen_asr import Qwen3ASRModel  # noqa: E402

log = logging.getLogger("shuoshuo")
MAX_BODY = 64 << 20  # 16k/16-bit 单声道约 35 分钟


class Hotwords:
    """热词表：每行一个词，# 开头为注释；文件改了下一次请求自动生效，不用重启。"""

    def __init__(self, path):
        self.path, self.mtime, self.context = path, None, ""

    def get(self):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return ""
        if mtime != self.mtime:
            with open(self.path, encoding="utf-8") as f:
                words = [s.strip() for s in f if s.strip() and not s.lstrip().startswith("#")]
            self.mtime, self.context = mtime, "\n".join(words)
            log.info("热词 %d 个", len(words))
        return self.context


class Recognizer:
    def __init__(self, model, device, language):
        self.model = Qwen3ASRModel.from_pretrained(
            model, dtype=torch.bfloat16, device_map=device, max_inference_batch_size=1)
        self.language = language or None
        self.lock = threading.Lock()  # 一张卡同一时间只跑一条

    def __call__(self, wav, sr, context=""):
        with self.lock:
            # 按时长限制生成长度：正常语速每秒不到 5 个 token，防止偶发的重复输出拖很久
            self.model.max_new_tokens = 32 + int(12 * len(wav) / sr)
            r = self.model.transcribe(audio=(wav, sr), context=context, language=self.language)[0]
        return r.language, r.text


def read_wav(data):
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("只支持 16-bit 单声道 WAV")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return pcm.astype(np.float32) / 32768, w.getframerate()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        srv = self.server
        if self.path != "/asr":
            return self._reply(404, {"error": "not found"})
        size = int(self.headers.get("Content-Length", 0))
        if size > MAX_BODY:
            return self._reply(413, {"error": "音频太长"})
        body = self.rfile.read(size)  # 先读完再回复：客户端还在上传时就断开，它只会看到 connection reset
        auth = self.headers.get("Authorization", "").encode()
        if srv.token and not hmac.compare_digest(auth, f"Bearer {srv.token}".encode()):
            return self._reply(401, {"error": "token 不对"})
        try:
            wav, sr = read_wav(body)
        except Exception as e:
            return self._reply(400, {"error": f"音频解析失败：{e}"})

        t0 = time.perf_counter()
        try:
            lang, text = srv.recognizer(wav, sr, srv.hotwords.get())
        except Exception as e:
            log.exception("识别出错")
            return self._reply(500, {"error": str(e)})
        ms = (time.perf_counter() - t0) * 1000
        log.info("%.1fs 音频 → %d 字，%.0f ms %s", len(wav) / sr, len(text), ms, text if srv.verbose else "")
        self._reply(200, {"text": text, "language": lang, "ms": round(ms)})

    def _reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 不要 http.server 默认的逐请求访问日志
        pass


def main():
    ap = argparse.ArgumentParser(description="说说 · 语音识别服务")
    ap.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B", help="本地模型目录或 HuggingFace 模型名")
    ap.add_argument("--host", default="127.0.0.1", help="其他设备要连就用 0.0.0.0（记得设 token）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default=os.environ.get("SHUOSHUO_TOKEN", ""), help="访问令牌，也可用环境变量 SHUOSHUO_TOKEN")
    ap.add_argument("--hotwords", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "hotwords.txt"))
    ap.add_argument("--language", default="", help="留空自动识别（中英混说就留空），也可填 Chinese / English 等")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("-v", "--verbose", action="store_true", help="日志里显示识别出的文字")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    log.info("加载模型 %s ...", args.model)
    t0 = time.perf_counter()
    recognizer = Recognizer(args.model, args.device, args.language)
    recognizer(np.zeros(16000, np.float32), 16000)  # 预热：首次推理有 CUDA 初始化开销
    log.info("模型就绪，用时 %.0fs", time.perf_counter() - t0)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.recognizer, srv.hotwords, srv.token, srv.verbose = recognizer, Hotwords(args.hotwords), args.token, args.verbose
    srv.hotwords.get()
    if args.host not in ("127.0.0.1", "localhost") and not args.token:
        log.warning("对外监听但没设 token，同一网络里谁都能用")
    log.info("监听 http://%s:%d", args.host, args.port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
