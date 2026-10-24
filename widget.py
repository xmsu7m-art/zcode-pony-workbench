# -*- coding: utf-8 -*-
"""ZCode 小马工作台 —— 系统级悬浮侧边栏启动器(Marvis 式)。

用 Edge --app 模式打开无边框独立窗口;窗口的停靠/置顶/折叠由 server.py
通过 Win32 SetWindowPos 管理(页面按钮 -> /win 接口)。
"""
import ctypes
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
URL = "http://127.0.0.1:8799/widget"
TITLE = "ZCode 小马工作台"
user32 = ctypes.windll.user32


def server_ok():
    try:
        urllib.request.urlopen("http://127.0.0.1:8799/health", timeout=1)
        return True
    except Exception:
        return False


def ensure_server():
    if server_ok():
        return
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, os.path.join(BASE, "server.py")],
                     creationflags=flags, close_fds=True)
    for _ in range(40):
        time.sleep(0.3)
        if server_ok():
            return


def find_edge():
    for p in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ):
        if os.path.isfile(p):
            return p
    return None


def launch_browser():
    exe = find_edge()
    if not exe:
        os.startfile(URL)  # 兜底:默认浏览器打开(无停靠能力)
        return False
    profile = os.path.join(BASE, "edge-profile")
    args = [
        exe,
        "--app=" + URL,
        "--user-data-dir=" + profile,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,IntensiveWakeUpThrottling",
        # 常驻悬浮窗必须禁掉后台节流,否则被遮挡时页面会被冻结
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-size=380,1400",
        "--window-position=2180,0",
    ]
    subprocess.Popen(args, close_fds=True)
    return True


def close_old_windows():
    """强杀历史残留的工作台 Edge(独立 profile),确认退净再返回。"""
    try:
        import psutil
        targets = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "msedge" not in (p.info["name"] or "").lower():
                    continue
                cl = p.info["cmdline"] or []
                if any("edge-profile" in c for c in cl):
                    targets.append(p)
            except Exception:
                continue
        for p in targets:
            try:
                p.kill()
            except Exception:
                pass
        gone, alive = psutil.wait_procs(targets, timeout=5)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(1.0)


def main():
    ensure_server()
    close_old_windows()
    time.sleep(1.0)
    launch_browser()
    # 等窗口出现,交给 server 的停靠线程接管(置顶+贴右缘)
    for _ in range(30):
        time.sleep(0.5)
        if user32.FindWindowW(None, TITLE):
            break


if __name__ == "__main__":
    main()
