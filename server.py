# -*- coding: utf-8 -*-
"""Workbench live server: serves workbench.html and tails events.jsonl.

Endpoints:
  GET /                -> workbench.html
  GET /events?since=N  -> {"pos": <byte size>, "events": [lines after byte N]}
  GET /health          -> {"ok": true}
"""
import json
import glob
import os
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import ctypes
import ctypes.wintypes

# 64 位下必须声明 argtypes,否则 HWND_TOPMOST(-1) 被按 32 位传参 → 句柄被截断成非法值
ctypes.windll.user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
ctypes.windll.user32.SetWindowPos.restype = ctypes.wintypes.BOOL

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "workbench.html")
WIDGET = os.path.join(BASE, "widget.html")
EVENTS = os.path.join(BASE, "events.jsonl")
WALLET = os.path.join(BASE, "wallet.json")
ROLLOUT_DIR = os.path.expandvars(r"%USERPROFILE%\.zcode\cli\rollout")
PORT = 8799

WIN_TITLE = "ZCode 小马工作台"
WIN_W = 380       # DIP 设计宽度
HANDLE_W = 34     # 折叠后露出宽度
_win_state = {"collapsed": False}
_keeper = {"ticks": 0, "last_hwnd": 0, "last_action": "", "alive_since": 0}
_stats = {"total": 0, "rate": 0.0, "active": False, "sid": ""}
_hits = {"widget": 0.0}      # 侧边栏页面最近一次心跳(秒级时间戳)
_relaunch = {"last": 0.0}    # 上次自动重启侧边栏的时刻(防重启风暴)
_EDGE_FLAGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=IntensiveWakeUpThrottling",
]


def load_wallet():
    try:
        with open(WALLET, "r", encoding="utf-8") as f:
            return int(json.load(f).get("total_tokens", 10000000))
    except Exception:
        return 10000000


def save_wallet(total):
    try:
        with open(WALLET, "w", encoding="utf-8") as f:
            json.dump({"total_tokens": int(total)}, f)
    except Exception:
        pass


_stats["wallet"] = load_wallet()


def tail_usage(path, off):
    """从字节偏移增量解析转录行,返回(新偏移, 计费增量, 输出增量)。
    口径:计费 = (inputTokens - cacheRead) + cacheWrite + outputTokens;不含缓存读取。"""
    billable = 0
    output = 0
    try:
        size = os.path.getsize(path)
        if size <= off:
            return off, 0, 0
        with open(path, "rb") as f:
            f.seek(off)
            chunk = f.read(size - off)
        if not chunk:
            return off, 0, 0
        consumed = 0
        for raw in chunk.split(b"\n")[:-1]:  # 最后一段可能是半行,留待下次
            consumed += len(raw) + 1
            if not raw.strip():
                continue
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                continue
            u = (d.get("response") or {}).get("usage") or {}
            inp = u.get("inputTokens") or 0
            out = u.get("outputTokens") or 0
            cr = u.get("cacheReadTokens") or 0
            cw = u.get("cacheWriteTokens") or 0
            b = (inp - cr) + cw + out
            if b < 0:
                b = inp + out
            billable += b
            output += out
        return off + consumed, billable, output
    except Exception:
        return off, 0, 0


def stats_sampler():
    import time as _t
    st = {}  # file -> {"off": int, "billable": int, "output": int}
    last_bill, last_out, last_ts = 0, 0, _t.time()
    ema_bill, ema_out, last_delta = 0.0, 0.0, 0.0
    first = True
    while True:
        try:
            files = glob.glob(os.path.join(ROLLOUT_DIR, "model-io-session_*.jsonl"))
            for p in files:
                ent = st.setdefault(p, {"off": 0, "billable": 0, "output": 0})
                new_off, b, o = tail_usage(p, ent["off"])
                if new_off < ent["off"]:  # 文件被轮换/清空:从头重新累计
                    new_off, b, o = tail_usage(p, 0)
                    ent["billable"], ent["output"] = b, o
                else:
                    ent["billable"] += b
                    ent["output"] += o
                ent["off"] = new_off
                if b > 0 or o > 0:
                    last_delta = _t.time()
            bill = sum(v["billable"] for v in st.values())
            outp = sum(v["output"] for v in st.values())
            now = _t.time()
            dt = now - last_ts
            if first:
                # 首轮是历史回灌:只定基线,不计速度
                first = False
                last_bill, last_out, last_ts = bill, outp, now
            elif dt >= 1.5:
                db, do = bill - last_bill, outp - last_out
                ib = max(0.0, db / dt)
                io_ = max(0.0, do / dt)
                if ib > 0 or io_ > 0:
                    ema_bill = ema_bill * 0.5 + ib * 0.5
                    ema_out = ema_out * 0.5 + io_ * 0.5
                else:
                    ema_bill *= 0.5
                    ema_out *= 0.5
                last_bill, last_out, last_ts = bill, outp, now
            _stats["total"] = bill
            _stats["output_total"] = outp
            _stats["rate"] = round(ema_out, 1)
            _stats["rate_billable"] = round(ema_bill, 1)
            _stats["active"] = (now - last_delta) < 12
            _stats["wallet"] = load_wallet()
        except Exception:
            pass
        time.sleep(2)


def tail_events(since):
    try:
        size = os.path.getsize(EVENTS)
    except OSError:
        return 0, []
    if since is None or since < 0 or since > size:
        since = max(0, size - 64 * 1024)
    out = []
    with open(EVENTS, "rb") as f:
        if since:
            f.seek(since)
        chunk = f.read().decode("utf-8", errors="replace")
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return size, out[-500:]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/health":
                self._send(200, b'{"ok":true}', "application/json")
            elif u.path == "/events":
                q = parse_qs(u.query)
                if q.get("src", [""])[0] == "widget":
                    _hits["widget"] = time.time()
                since = int(q.get("since", ["0"])[0] or 0)
                pos, evs = tail_events(since)
                body = json.dumps({"pos": pos, "events": evs}, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json")
            elif u.path == "/widget":
                with open(WIDGET, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif u.path == "/stats":
                out = dict(_stats)
                out["used"] = _stats["total"]
                out["remaining"] = max(0, _stats["wallet"] - _stats["total"])
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json")
            elif u.path == "/wallet":
                try:
                    ln = int(self.headers.get("Content-Length", 0) or 0)
                    req = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
                    total = int(req.get("total_tokens", 0))
                    if total > 0:
                        save_wallet(total)
                        _stats["wallet"] = total
                    body = json.dumps({"ok": True, "wallet": _stats["wallet"]}).encode("utf-8")
                    self._send(200, body, "application/json")
                except Exception as e:
                    self._send(400, str(e).encode("utf-8"), "text/plain")
            elif u.path == "/win":
                q = parse_qs(u.query)
                body = json.dumps(handle_win_cmd((q.get("cmd", [""])[0])), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json")
            elif u.path == "/":
                with open(HTML, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(500, str(e).encode("utf-8"), "text/plain")
            except Exception:
                pass

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/wallet":
                ln = int(self.headers.get("Content-Length", 0) or 0)
                req = json.loads(self.rfile.read(ln).decode("utf-8")) if ln else {}
                total = int(req.get("total_tokens", 0))
                if total > 0:
                    save_wallet(total)
                    _stats["wallet"] = total
                self._send(200, json.dumps({"ok": True, "wallet": _stats["wallet"]}).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            try:
                self._send(500, str(e).encode("utf-8"), "text/plain")
            except Exception:
                pass

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------- 窗口管理(停靠/置顶/折叠) ----------------
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
EDGE_CLASSES = ("Chrome_WidgetWin_1",)
_pid_cache = {"pids": set(), "at": 0.0}


def workbench_pids():
    """属于本工作台独立 Edge profile 的进程 pid(缓存 10s)。"""
    import time as _t
    now = _t.time()
    if now - _pid_cache["at"] < 10:
        return _pid_cache["pids"]
    pids = set()
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "msedge" not in (p.info["name"] or "").lower():
                    continue
                cl = p.info["cmdline"] or []
                if any("edge-profile" in c for c in cl):
                    pids.add(p.info["pid"])
            except Exception:
                continue
    except Exception:
        pass
    _pid_cache["pids"] = pids
    _pid_cache["at"] = now
    return pids


def find_widget_windows():
    """枚举工作台窗口:按类名 + pid 匹配,全程不向其他进程发消息。"""
    out = []
    try:
        u = ctypes.windll.user32
        pids = workbench_pids()
        if not pids:
            return out

        @WNDENUMPROC
        def cb(h, l):
            try:
                if not u.IsWindowVisible(h):
                    return True
                cls = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(h, cls, 64)  # 不发消息,不会阻塞
                if cls.value not in EDGE_CLASSES:
                    return True
                pid = ctypes.wintypes.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value in pids:
                    out.append(h)
            except Exception:
                pass
            return True
        u.EnumWindows(cb, 0)
    except Exception:
        pass
    return out


def close_widget_windows():
    """关闭本工作台独立 profile 的全部 Edge 进程。"""
    killed = 0
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "msedge" not in (p.info["name"] or "").lower():
                    continue
                cl = p.info["cmdline"] or []
                if any("edge-profile" in c for c in cl):
                    p.terminate()
                    killed += 1
            except Exception:
                continue
    except Exception:
        pass
    return killed


def work_top():
    class R(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = R()
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0)
        return rc.t
    except Exception:
        return 0


def dock_widget(hwnd):
    try:
        u = ctypes.windll.user32
        _keeper["last_hwnd"] = hwnd
        if u.IsIconic(hwnd):  # 被最小化(如被其他程序/用户):自动恢复,不抢焦点
            u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            _keeper["last_action"] = "restore"
            return  # 下一轮 tick 再精确停靠
        rect = ctypes.wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        if w <= 50:
            _keeper["last_action"] = "skip-w%d" % w
            return
        scale = w / float(WIN_W)
        screen_w = u.GetSystemMetrics(0)
        x = screen_w - (int(HANDLE_W * scale) if _win_state["collapsed"] else int(WIN_W * scale))
        SWP_NOSIZE, SWP_NOACTIVATE = 0x1, 0x10
        ret = u.SetWindowPos(hwnd, -1, x, work_top(), 0, 0,
                             SWP_NOSIZE | SWP_NOACTIVATE)  # -1 = HWND_TOPMOST,同步调用(异步模式在此环境失败)
        _keeper["last_action"] = "move x=%s ret=%s err=%s" % (x, ret, ctypes.windll.kernel32.GetLastError())
    except Exception as e:
        _keeper["last_action"] = "exc: %s" % e


def find_edge_exe():
    for p in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ):
        if os.path.isfile(p):
            return p
    return None


def relaunch_widget_browser():
    """强杀本工作台独立 profile 的 Edge 并带反节流参数重启(页面僵死时自愈)。"""
    import subprocess
    try:
        import psutil
        targets = []
        for pr in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "msedge" not in (pr.info["name"] or "").lower():
                    continue
                if any("edge-profile" in c for c in (pr.info["cmdline"] or [])):
                    targets.append(pr)
            except Exception:
                continue
        for pr in targets:
            try:
                pr.kill()
            except Exception:
                pass
        psutil.wait_procs(targets, timeout=5)
    except Exception:
        pass
    time.sleep(1.0)
    exe = find_edge_exe()
    if not exe:
        return False
    args = [exe,
            "--app=http://127.0.0.1:8799/widget",
            "--user-data-dir=" + os.path.join(BASE, "edge-profile"),
            "--no-first-run", "--no-default-browser-check",
            "--window-size=380,1400", "--window-position=2180,0"] + _EDGE_FLAGS
    subprocess.Popen(args, close_fds=True)
    return True


def win_keeper():
    import time as _t
    _keeper["alive_since"] = int(_t.time() * 1000)
    while True:
        try:
            wins = find_widget_windows()
            if wins:
                _keeper["ticks"] += 1
                _keeper["last_hwnd"] = wins[0]
                for h in wins:
                    threading.Thread(target=dock_widget, args=(h,), daemon=True).start()
                _keeper["last_action"] = "dock %d window(s)" % len(wins)
                # 侧边栏对账:窗口在,但页面 45 秒没有心跳 → 页面被冻结,自动重启侧边栏
                now = _t.time()
                age = now - _hits["widget"] if _hits["widget"] else -1
                _keeper["widget_hit_age"] = int(age)
                if (now - _keeper["alive_since"] / 1000.0 > 30
                        and now - _relaunch["last"] > 120
                        and (age < 0 or age > 45)):
                    if relaunch_widget_browser():
                        _relaunch["last"] = now
                        _keeper["last_action"] = "relaunch-edge (page frozen %ss)" % int(age)
            else:
                _keeper["last_action"] = "no-window"
        except Exception as e:
            _keeper["last_action"] = "loop-exc: %s" % e
        time.sleep(2.5)


def handle_win_cmd(cmd):
    if cmd in ("toggle", "collapse", "expand"):
        if cmd == "toggle":
            _win_state["collapsed"] = not _win_state["collapsed"]
        else:
            _win_state["collapsed"] = (cmd == "collapse")
        wins = find_widget_windows()
        for h in wins:
            threading.Thread(target=dock_widget, args=(h,), daemon=True).start()
        rect_txt = ""
        try:
            if wins:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(wins[0], ctypes.byref(rect))
                rect_txt = "%s,%s,%s,%s" % (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass
        return {"ok": True, "collapsed": _win_state["collapsed"], "windows": len(wins), "rect": rect_txt}
    if cmd == "probe":
        hwnd = find_widget_window()
        out = {"hwnd": hwnd}
        try:
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            out["before"] = "%s,%s,%s,%s" % (rect.left, rect.top, rect.right, rect.bottom)
            u = ctypes.windll.user32
            SWP = 0x1 | 0x4 | 0x10
            ret = u.SetWindowPos(hwnd, 0, 100, 100, 0, 0, SWP)
            out["ret"] = ret
            out["err"] = ctypes.windll.kernel32.GetLastError()
            u.GetWindowRect(hwnd, ctypes.byref(rect))
            out["after"] = "%s,%s,%s,%s" % (rect.left, rect.top, rect.right, rect.bottom)
        except Exception as e:
            out["exc"] = str(e)
        return out
    if cmd == "keeper":
        return dict(_keeper)
    if cmd == "threads":
        import threading as _th
        return {"threads": [t.name for t in _th.enumerate()],
               "count": _th.active_count()}
    if cmd == "full":
        try:
            os.startfile("http://127.0.0.1:8799/")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "err": str(e)}
    if cmd == "close":
        close_widget_windows()
        return {"ok": True}
    return {"ok": False, "err": "unknown cmd"}


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    threading.Thread(target=win_keeper, daemon=True).start()
    threading.Thread(target=stats_sampler, daemon=True).start()
    Server(("127.0.0.1", PORT), Handler).serve_forever()
