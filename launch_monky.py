import argparse
import atexit
import os
import signal
import sys
import time
from pathlib import Path

from server import HubController, HOST, PORT


ROOT = Path(__file__).resolve().parent
PID_FILE = ROOT / "monky.pid"


def _read_pid() -> int | None:
    try:
        data = PID_FILE.read_text().strip()
        if not data:
            return None
        return int(data)
    except FileNotFoundError:
        return None
    except ValueError:
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def _write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def _remove_pid_file() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def _wait_for_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.2)
    return not _process_alive(pid)


def main():
    parser = argparse.ArgumentParser(description="Launch the AIVA provider hub")
    parser.add_argument("--headless", action="store_true", help="Run without GUI or tray")
    parser.add_argument("--no-gui", action="store_true", help="Start without showing the control panel")
    parser.add_argument("--stop", action="store_true", help="Stop a running hub instance")
    parser.add_argument("--status", action="store_true", help="Check whether the hub is already running")
    args = parser.parse_args()

    existing_pid = _read_pid()

    if args.stop:
        if existing_pid and _process_alive(existing_pid):
            print(f"Stopping running hub (pid={existing_pid})…")
            os.kill(existing_pid, signal.SIGINT)
            if not _wait_for_exit(existing_pid):
                # Fall back to SIGTERM if the process ignored SIGINT
                os.kill(existing_pid, signal.SIGTERM)
                if not _wait_for_exit(existing_pid, timeout=5.0):
                    print("Process did not terminate; manual intervention required.")
                    return
            print("Hub stopped.")
        else:
            print("No running hub instance found.")
        _remove_pid_file()
        return

    if args.status:
        if existing_pid and _process_alive(existing_pid):
            print(f"Hub running (pid={existing_pid}) at http://{HOST}:{PORT}")
        else:
            print("Hub not running.")
            _remove_pid_file()
        return

    if existing_pid and _process_alive(existing_pid):
        print(f"Hub already running (pid={existing_pid}) at http://{HOST}:{PORT}. Use --stop to restart.")
        return

    controller = HubController(host=HOST, port=PORT)

    def _handle_termination(signum, frame):  # noqa: ANN001
        controller.shutdown()

    signal.signal(signal.SIGTERM, _handle_termination)

    _write_pid()
    atexit.register(_remove_pid_file)

    try:
        controller.run(headless=args.headless, open_gui=not args.no_gui)
    finally:
        _remove_pid_file()


if __name__ == "__main__":
    main()
