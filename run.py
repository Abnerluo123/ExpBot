"""ExpBot · local desktop launcher. Python stdlib + Pillow, no frontend build needed."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def open_window(url: str, data_dir: Path | None = None):
    desktop = ROOT / 'desktop/SCNU.Window.exe'
    if desktop.is_file():
        profile = Path(data_dir or ROOT / 'data') / 'webview-profile'
        subprocess.Popen([str(desktop), url, str(profile), str(ROOT / 'app/web/icon.ico')], cwd=str(ROOT),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    candidates = [Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                  Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe"]
    for exe in candidates:
        if exe.is_file():
            profile = Path(data_dir or ROOT / 'data') / 'desktop-profile'
            process = subprocess.Popen([str(exe), "--app=" + url, "--window-size=1440,960", "--no-first-run",
                                        "--user-data-dir=" + str(profile)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from app.windows_icon import schedule_window_icon
            schedule_window_icon(process.pid, ROOT / 'app/web/icon.ico', ROOT / 'ExpBot.exe')
            return
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="ExpBot")
    parser.add_argument("--no-browser", action="store_true", help="仅启动本地服务")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, help="独立本地数据目录，用于测试")
    args = parser.parse_args()
    from app.config.settings import DATA_DIR
    from app.server import AppServer, Workspace
    data = (args.data_dir or DATA_DIR).resolve()
    data.mkdir(parents=True, exist_ok=True)
    info_path = data / "running.json"
    if not args.no_browser and info_path.exists():
        try:
            record = json.loads(info_path.read_text(encoding="utf-8"))
            with urllib.request.urlopen(record["url"] + "health", timeout=2) as response:
                health = json.load(response)
            if health.get("app") == "qingbao" and health.get("pid") == record["pid"]:
                open_window(record["url"], data)
                return
        except Exception:
            pass
    workspace = Workspace(data)
    server = AppServer(("127.0.0.1", args.port), workspace=workspace)
    url = f"http://127.0.0.1:{server.server_port}/"
    record = {"url": url, "pid": os.getpid(), "root": str(ROOT)}
    info_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    print("APP_URL=" + url, flush=True)
    print("Only loopback access; originals remain local. Close with the in-app power button.", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: open_window(url, data)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if info_path.exists():
            try:
                saved = json.loads(info_path.read_text(encoding="utf-8"))
                if saved.get("pid") == os.getpid():
                    info_path.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No credentials or document contents enter launcher logs.
        log = ROOT / "startup-error.txt"
        log.write_text(type(exc).__name__ + ": " + str(exc), encoding="utf-8")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "启动未完成，请查看软件目录的 startup-error.txt。\n请勿放在只读或压缩文件夹中运行。", "ExpBot · 启动提示", 0x10)
        raise
