import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SERVER_PATH = BASE_DIR / "server.py"
LOG_DIR = BASE_DIR / "logs"


def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("config.json not found. Run setup_wizard.py first.")
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def enabled_apps(config):
    features = config.get("features", {})
    return [app for app in ["work", "home", "mobile"] if features.get(app, True)]


def start_server(config):
    python = sys.executable
    if os.name == "nt":
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)
    LOG_DIR.mkdir(exist_ok=True)
    log_file = (LOG_DIR / "monky-server.log").open("a", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    process = subprocess.Popen([python, str(SERVER_PATH)], stdout=log_file, stderr=log_file, cwd=BASE_DIR, env=env)
    return process


def wait_for_health(host: str, port: int, timeout: int = 30):
    url = f"http://{host}:{port}/health"
    start = time.time()
    while time.time() - start < timeout:
        try:
            res = requests.get(url, timeout=2)
            if res.ok:
                return True
        except requests.RequestException:
            time.sleep(1)
        else:
            time.sleep(1)
    return False


def open_default(config):
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = int(config.get("server", {}).get("port", 5050))
    apps = enabled_apps(config)
    default_app = config.get("apps", {}).get("default", "work")
    if default_app not in apps and apps:
        default_app = apps[0]
    if len(apps) > 1:
        path = "/"
    else:
        path = {"work": "/work", "home": "/home", "mobile": "/m"}.get(default_app, "/")
    webbrowser.open(f"http://{host}:{port}{path}")


def main():
    config = load_config()
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = int(config.get("server", {}).get("port", 5050))
    process = start_server(config)
    if not wait_for_health(host, port):
        print("MONKY server failed to start within timeout", file=sys.stderr)
        return
    open_default(config)
    print("MONKY server online at http://%s:%s" % (host, port))
    # keep launcher alive briefly to avoid premature exit logs closing
    time.sleep(2)


if __name__ == "__main__":
    main()
