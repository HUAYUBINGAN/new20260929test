#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地字幕工具 —— 服务端
零第三方依赖，仅使用 Python 标准库。
启动后监听本机回环地址，浏览器打开即可使用。
"""

import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote

# ---------------------------------------------------------------- 基础路径
BASE = Path(__file__).resolve().parent
BIN_DIR = BASE / "bin"
MODEL_DIR = BASE / "models"
UI_DIR = BASE / "ui"
OUT_DIR = BASE / "output"
TMP_DIR = BASE / "tmp"
CONFIG_PATH = BASE / "config.json"
WHISPER_CLI = BIN_DIR / "whisper-cli.exe"

for d in (OUT_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "ffmpeg_path": "",
    "cuda_dirs": [],
    "model": "models/ggml-large-v3.bin",
    "language": "zh",
    "format": "srt",
    "vad": True,
    "use_gpu": True,
    "prompt": "",
    "threads": 8,
}

# ---------------------------------------------------------------- 任务状态
JOB = {
    "running": False,
    "stage": "idle",        # idle | extract | transcribe | done | error | cancelled
    "progress": 0,
    "message": "待命",
    "log": [],
    "error": "",
    "result": None,         # {"path": str, "name": str, "chars": int}
    "elapsed": 0.0,
    "video": "",
}
JOB_LOCK = threading.Lock()
CANCEL = threading.Event()
CURRENT_PROC = None


# ---------------------------------------------------------------- 工具函数
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def log(msg):
    with JOB_LOCK:
        JOB["log"].append(msg)
        if len(JOB["log"]) > 400:
            del JOB["log"][:-400]


def set_job(**kw):
    with JOB_LOCK:
        JOB.update(kw)


def snapshot():
    with JOB_LOCK:
        return {
            "running": JOB["running"],
            "stage": JOB["stage"],
            "progress": JOB["progress"],
            "message": JOB["message"],
            "log": list(JOB["log"][-120:]),
            "error": JOB["error"],
            "result": JOB["result"],
            "elapsed": round(JOB["elapsed"], 1),
            "video": JOB["video"],
        }


def find_ffmpeg(cfg=None):
    """探测 ffmpeg：配置 → PATH → 已知位置"""
    cfg = cfg or load_config()
    p = (cfg.get("ffmpeg_path") or "").strip()
    if p and Path(p).exists():
        return p
    w = shutil.which("ffmpeg")
    if w:
        return w
    candidates = [
        r"D:\ComfyUI\ComfyUI_windows_portable-G314\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def find_cuda_dirs(cfg=None):
    """探测 CUDA 运行库目录（13.x 把 dll 放在 bin\\x64）"""
    cfg = cfg or load_config()
    dirs = []
    for p in cfg.get("cuda_dirs") or []:
        if p and Path(p).exists():
            dirs.append(p)
    root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if root.exists():
        try:
            versions = sorted(
                [v for v in root.iterdir() if v.is_dir()],
                key=lambda v: v.name,
                reverse=True,
            )
        except OSError:
            versions = []
        for v in versions:
            for sub in ("bin\\x64", "bin"):
                d = v / sub
                try:
                    if d.exists() and any(d.glob("cudart64_*.dll")):
                        dirs.append(str(d))
                except OSError:
                    pass
    # 去重保序
    seen, out = set(), []
    for d in dirs:
        if d.lower() not in seen:
            seen.add(d.lower())
            out.append(d)
    return out


def find_vad_model():
    """查找 VAD 模型（--vad 防幻觉需要它）"""
    for p in sorted(MODEL_DIR.glob("ggml-silero*.bin")):
        return p
    return None


def build_env():
    """构造子进程环境：把 CUDA 运行库加进 PATH"""
    env = os.environ.copy()
    extra = find_cuda_dirs()
    if extra:
        env["PATH"] = ";".join(extra) + ";" + env.get("PATH", "")
    return env


def safe_stem(name):
    """把文件名转成安全的输出前缀（保留中文）"""
    stem = Path(name).stem
    stem = re.sub(r'[\\/:*?"<>|]', "_", stem).strip() or "subtitle"
    return stem


# ---------------------------------------------------------------- 缓存维护
def _scan_dir(d):
    """列出目录下的直系文件（不下钻子目录），返回 (条目列表, 总字节)"""
    items, total = [], 0
    try:
        for p in sorted(d.iterdir()):
            if p.is_file():
                try:
                    sz = p.stat().st_size
                except OSError:
                    sz = 0
                items.append({"name": p.name, "size": sz})
                total += sz
    except OSError:
        pass
    return items, total


def cache_report():
    """只读统计 tmp / output 的占用"""
    tmp_items, tmp_total = _scan_dir(TMP_DIR)
    out_items, out_total = _scan_dir(OUT_DIR)
    with JOB_LOCK:
        running = JOB["running"]
    return {
        "tmp": {"dir": str(TMP_DIR), "count": len(tmp_items),
                "size": tmp_total, "files": tmp_items},
        "output": {"dir": str(OUT_DIR), "count": len(out_items),
                   "size": out_total, "files": out_items},
        "total": tmp_total + out_total,
        "running": running,
    }


def clear_cache(targets):
    """删除 tmp / output 里的文件。

    安全约束（三重）：
      1. 只处理 tmp 与 output 两个目录，bin / models / config.json 永不涉及；
      2. 只删直系文件，不下钻子目录；
      3. 路径 resolve 后其父目录必须仍是目标目录（挡住符号链接外逃）。
    """
    removed, freed, errors = [], 0, []
    for tag, d in (("tmp", TMP_DIR), ("output", OUT_DIR)):
        if tag not in targets:
            continue
        try:
            base = d.resolve()
            entries = [p for p in d.iterdir() if p.is_file()]
        except OSError as e:
            errors.append(f"{tag}: {e}")
            continue
        for p in entries:
            try:
                if p.resolve().parent != base:
                    errors.append(f"{p.name}: 路径校验未通过，已跳过")
                    continue
                sz = p.stat().st_size
                p.unlink()
                removed.append(str(p))
                freed += sz
            except OSError as e:
                errors.append(f"{p.name}: {e}")
    return removed, freed, errors


# ---------------------------------------------------------------- 转写任务
def worker(video_path: str, cfg: dict):
    global CURRENT_PROC
    t0 = time.time()
    video = Path(video_path)
    try:
        if not video.exists():
            raise RuntimeError(f"视频文件不存在：{video}")

        ffmpeg = find_ffmpeg(cfg)
        audio = TMP_DIR / "audio.wav"
        if ffmpeg:
            set_job(stage="extract", progress=0, message="正在抽取音轨…")
            log(f"[1/2] 抽音轨：{ffmpeg}")
            cmd = [
                ffmpeg, "-v", "error", "-y", "-i", str(video),
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
            ]
            fflog = TMP_DIR / "ffmpeg.log"
            with open(fflog, "w", encoding="utf-8", errors="replace") as lf:
                proc = subprocess.Popen(
                    cmd, stdout=lf, stderr=subprocess.STDOUT,
                    env=build_env(), cwd=str(BIN_DIR),
                )
                with JOB_LOCK:
                    CURRENT_PROC = proc
                while proc.poll() is None:
                    if CANCEL.is_set():
                        proc.kill()
                        proc.wait()
                        raise InterruptedError()
                    time.sleep(0.15)
            err = fflog.read_text(encoding="utf-8", errors="replace") if fflog.exists() else ""
            if proc.returncode != 0 or not audio.exists():
                raise RuntimeError("抽音轨失败：\n" + err[:800])
            log(f"      音频就绪（{audio.stat().st_size/1048576:.1f} MB）")
        else:
            set_job(stage="extract", message="未找到 ffmpeg，直接把视频交给模型…")
            log("[1/2] 未找到 ffmpeg，跳过抽音轨，直接把视频交给模型")
            audio = video

        if CANCEL.is_set():
            raise InterruptedError()

        # ---- 转写 ----
        model = cfg.get("model") or "models/ggml-large-v3.bin"
        model_path = Path(model)
        if not model_path.is_absolute():
            model_path = BASE / model
        if not model_path.exists():
            raise RuntimeError(f"模型文件不存在：{model_path}")
        if not WHISPER_CLI.exists():
            raise RuntimeError(f"缺少 whisper-cli.exe：{WHISPER_CLI}")

        lang = (cfg.get("language") or "zh").strip()
        fmt = (cfg.get("format") or "srt").lower()
        out_prefix = OUT_DIR / safe_stem(video.name)

        cmd = [str(WHISPER_CLI), "-m", str(model_path), "-f", str(audio),
               "-of", str(out_prefix), "-t", str(cfg.get("threads") or 8), "-pp"]
        cmd += ["-osrt"] if fmt == "srt" else (["-ovtt"] if fmt == "vtt" else ["-otxt"])
        if lang and lang != "auto":
            cmd += ["-l", lang]
        if cfg.get("vad"):
            vadm = find_vad_model()
            if vadm:
                cmd += ["--vad", "-vm", str(vadm)]
            else:
                log("      [提示] 未找到 VAD 模型（models/ggml-silero-*.bin），本次跳过 VAD")
        if not cfg.get("use_gpu", True):
            cmd += ["-ng"]
        p = (cfg.get("prompt") or "").strip()
        if p:
            cmd += ["--prompt", p]

        set_job(stage="transcribe", progress=0, message="正在识别…")
        log("[2/2] 开始识别：" + " ".join(f'"{c}"' if " " in c else c for c in cmd[1:]))

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, env=build_env(), cwd=str(BIN_DIR),
        )
        with JOB_LOCK:
            CURRENT_PROC = proc
        # 注册后立刻复查：堵住"取消请求恰好落在注册之前"的竞态窗口
        if CANCEL.is_set():
            proc.kill()
            raise InterruptedError()
        for line in proc.stdout:
            if CANCEL.is_set():
                proc.kill()
                raise InterruptedError()
            line = line.rstrip()
            if not line:
                continue
            m = re.search(r"progress\s*=\s*(\d+)\s*%", line)
            if m:
                pct = int(m.group(1))
                set_job(progress=pct, elapsed=time.time() - t0,
                        message=f"正在识别… {pct}%")
            elif "whisper_init" in line and "use gpu" in line:
                log("      " + line.strip())
            elif "ggml_cuda_init" in line or "CUDA0 total size" in line:
                log("      " + line.strip())
            elif "error" in line.lower() or "failed" in line.lower():
                log("      " + line.strip())
        proc.wait()
        if CANCEL.is_set():
            raise InterruptedError()
        if proc.returncode != 0:
            raise RuntimeError(f"识别失败（退出码 {proc.returncode}）")

        # ---- 产物 ----
        ext = {"srt": ".srt", "vtt": ".vtt", "txt": ".txt"}.get(fmt, ".srt")
        out_file = Path(str(out_prefix) + ext)
        if not out_file.exists():
            alt = list(OUT_DIR.glob(out_prefix.name + ".*"))
            if alt:
                out_file = alt[0]
            else:
                raise RuntimeError("未找到输出的字幕文件")

        text = out_file.read_text(encoding="utf-8", errors="replace")
        set_job(stage="done", progress=100, elapsed=time.time() - t0,
                message=f"完成！用时 {time.time()-t0:.1f} 秒",
                result={"path": str(out_file), "name": out_file.name,
                        "chars": len(text)})
        log(f"✅ 完成，输出：{out_file.name}（{len(text)} 字符）")

    except InterruptedError:
        set_job(stage="cancelled", running=False, message="已取消",
                elapsed=time.time() - t0)
        log("⏹ 任务已取消")
    except Exception as e:  # noqa: BLE001
        set_job(stage="error", running=False, error=str(e),
                message="出错了", elapsed=time.time() - t0)
        log("❌ " + str(e).replace("\n", " ")[:400])
    finally:
        with JOB_LOCK:
            JOB["running"] = False
            CURRENT_PROC = None


def start_job(video_path, cfg):
    with JOB_LOCK:
        if JOB["running"]:
            return False, "已有任务在运行"
        JOB.update({
            "running": True, "stage": "starting", "progress": 0,
            "message": "准备中…", "log": [], "error": "", "result": None,
            "elapsed": 0.0, "video": str(video_path),
        })
    CANCEL.clear()
    threading.Thread(target=worker, args=(video_path, cfg), daemon=True).start()
    return True, "已开始"


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "WhisperSubtitle/1.0"

    def log_message(self, *a):  # 静默访问日志
        pass

    # ---------- 响应助手 ----------
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path in ("/", "/index.html"):
            f = UI_DIR / "index.html"
            if not f.exists():
                return self._send(500, b"ui/index.html missing", "text/plain; charset=utf-8")
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")

        if path == "/api/status":
            cfg = load_config()
            ff = find_ffmpeg(cfg)
            cuda = find_cuda_dirs(cfg)
            return self._json({
                "job": snapshot(),
                "env": {
                    "ffmpeg": ff or "",
                    "ffmpeg_name": Path(ff).name if ff else "",
                    "ffmpeg_ok": bool(ff),
                    "cuda": cuda,
                    "gpu_ok": bool(cuda) and WHISPER_CLI.exists(),
                    "cli_ok": WHISPER_CLI.exists(),
                    "models": [str(p.relative_to(BASE)).replace("\\", "/")
                               for p in sorted(MODEL_DIR.glob("*.bin"))
                               if "silero" not in p.name.lower()
                               and "vad" not in p.name.lower()],
                },
                "config": cfg,
            })

        if path == "/api/progress":
            return self._json(snapshot())

        if path == "/api/cache_info":
            return self._json(cache_report())

        if path == "/api/result":
            with JOB_LOCK:
                res = JOB["result"]
            if not res:
                return self._json({"ok": False, "error": "暂无结果"}, 404)
            f = Path(res["path"])
            if not f.exists():
                return self._json({"ok": False, "error": "文件不存在"}, 404)
            return self._json({"ok": True, "name": f.name,
                               "text": f.read_text(encoding="utf-8", errors="replace")})

        if path == "/api/download":
            fn = (qs.get("f") or [""])[0]
            f = OUT_DIR / Path(fn).name
            if not f.exists():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            data = f.read_bytes()
            disp = "attachment; filename*=UTF-8''" + quote(f.name)
            return self._send(200, data, "application/octet-stream",
                              {"Content-Disposition": disp})

        if path == "/api/open_output":
            subprocess.Popen(["explorer", str(OUT_DIR)])
            return self._json({"ok": True})

        return self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path

        if path == "/api/config":
            body = self._read_json()
            cfg = load_config()
            for k in DEFAULT_CONFIG:
                if k in body:
                    cfg[k] = body[k]
            save_config(cfg)
            return self._json({"ok": True, "config": cfg})

        if path == "/api/start":
            body = self._read_json()
            cfg = load_config()
            for k in DEFAULT_CONFIG:
                if k in body:
                    cfg[k] = body[k]
            save_config(cfg)
            video = (body.get("video") or "").strip()
            if not video:
                return self._json({"ok": False, "error": "请先选择视频"}, 400)
            ok, msg = start_job(video, cfg)
            return self._json({"ok": ok, "message": msg}, 200 if ok else 409)

        if path == "/api/cancel":
            CANCEL.set()
            with JOB_LOCK:
                proc = CURRENT_PROC
            killed = False
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    killed = True
                except Exception:
                    pass
            return self._json({"ok": True, "killed": killed})

        if path == "/api/upload":
            raw = self.headers.get("X-Filename") or ""
            try:
                raw = unquote(raw)
            except Exception:
                pass
            if not raw:
                return self._json({"ok": False, "error": "缺少文件名"}, 400)
            dest = TMP_DIR / Path(raw).name
            n = int(self.headers.get("Content-Length") or 0)
            got = 0
            with open(dest, "wb") as fp:
                while got < n:
                    chunk = self.rfile.read(min(1 << 20, n - got))
                    if not chunk:
                        break
                    fp.write(chunk)
                    got += len(chunk)
            return self._json({"ok": True, "path": str(dest),
                               "name": dest.name,
                               "size": dest.stat().st_size})

        if path == "/api/clear_cache":
            with JOB_LOCK:
                busy = JOB["running"]
            if busy:
                return self._json(
                    {"ok": False, "error": "任务正在运行，请先取消或等它跑完"}, 409)
            body = self._read_json()
            raw = body.get("targets")
            targets = [t for t in raw if t in ("tmp", "output")] \
                if isinstance(raw, list) else []
            if not targets:
                targets = ["tmp", "output"]
            removed, freed, errors = clear_cache(targets)
            log(f"🧹 已清理 {len(removed)} 个文件，释放 {freed / 1048576:.1f} MB")
            return self._json({"ok": True, "removed": removed, "freed": freed,
                               "errors": errors, "cache": cache_report()})

        if path == "/api/reset_config":
            save_config(dict(DEFAULT_CONFIG))
            log("↺ 设置已恢复默认（程序与模型未受影响）")
            return self._json({"ok": True, "config": load_config()})

        return self._send(404, b"not found", "text/plain; charset=utf-8")


def pick_port(start=8848):
    for p in range(start, start + 60):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not WHISPER_CLI.exists():
        print(f"[警告] 未找到 {WHISPER_CLI}，转写将无法进行。")

    port = pick_port()
    url = f"http://127.0.0.1:{port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True

    print("=" * 52)
    print("  本地字幕工具已启动")
    print(f"  地址：{url}")
    print("  关闭本窗口即可退出")
    print("=" * 52)

    if not os.environ.get("NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        print("\n已退出。")


if __name__ == "__main__":
    main()
