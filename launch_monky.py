"""MONKY launcher utility.

Starts the Flask server in the background and opens the appropriate UI
based on the configuration stored in config.json.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict

import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SERVER_PATH = BASE_DIR / "server.py"
LOG_DIR = BASE_DIR / "logs"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("config.json not found. Run setup_wizard.py first.")
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_config(config: Dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def find_available_port(host: str, start_port: int, attempts: int = 20) -> int:
    candidate = start_port
    for _ in range(attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                candidate += 1
    raise RuntimeError("Unable to find open port for MONKY server")


def ensure_server_port(config: Dict[str, Any]) -> tuple[str, int]:
    server_cfg = config.setdefault("server", {})
    host = server_cfg.get("host", "127.0.0.1")
    desired_port = int(server_cfg.get("port", 5050))
    port = find_available_port(host, desired_port)
    if port != desired_port:
        print(f"[MONKY] Port {desired_port} unavailable; hopping to {port}")
        server_cfg["port"] = port
        save_config(config)
    return host, port


def start_server() -> subprocess.Popen[Any]:
    python = sys.executable
    if os.name == "nt":
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / "monky-server.log"
    with log_path.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [python, str(SERVER_PATH)],
            stdout=log_file,
            stderr=log_file,
            cwd=BASE_DIR,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

    return process


def stop_server(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def wait_for_health(host: str, port: int, timeout: int = 30) -> bool:
    url = f"http://{host}:{port}/health"
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = requests.get(url, timeout=2)
            if response.ok:
                return True
        except requests.RequestException:
            time.sleep(1)
        else:
            time.sleep(1)
    return False


def open_launcher(config: Dict[str, Any]) -> None:
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = int(config.get("server", {}).get("port", 5050))
    webbrowser.open(f"http://{host}:{port}/launch")


def main() -> None:
    config = load_config()
    host, port = ensure_server_port(config)

    process = start_server()
    try:
        if not wait_for_health(host, port):
            stop_server(process)
            raise RuntimeError("MONKY server failed to start within timeout")

        open_launcher(config)
        print(f"MONKY server online at http://{host}:{port}")
        time.sleep(2)
    except KeyboardInterrupt:
        print("\nLaunch cancelled. Shutting down server…")
        stop_server(process)
        raise SystemExit(130)
    except Exception:
        stop_server(process)
        raise


if __name__ == "__main__":
    main()
