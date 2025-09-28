"""First run configuration wizard for MONKY.

Provides both a Tkinter GUI and a CLI fallback so the wizard can run on
headless systems. The wizard persists values to config.json and can kick
off the MONKY launcher after saving.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, Iterable, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATE_PATH = BASE_DIR / "config_template.json"

DEFAULT_TEMPLATE: Dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 5050},
    "features": {
        "work": True,
        "home": True,
        "mobile": False,
        "assistant_embeddings": False,
    },
    "paths": {
        "desktop_export": "",
        "icons_dir": "",
        "avatar_path": "",
        "rag_docs_dir": "",
        "db_path": "monky.db",
    },
    "integrations": {
        "openrouter_key": "",
        "openrouter_model": "openrouter/auto",
        "genesis_key": "",
        "genesis_model": "llama-3.3-70b-instruct",
        "default_model": "monky-local",
        "openai_key": "",
        "openai_model": "gpt-4o-mini",
        "openai_base": "https://api.openai.com/v1",
        "qwen_key": "",
        "qwen_model": "qwen-turbo",
        "qwen_base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "ollama_host": "http://localhost:11434",
        "ollama_home_model": "llama3",
        "genesis_base": "https://api.ai.us.lmco.com/v1",
    },
    "security": {"vault_passphrase": "", "vault_pin": "1234"},
    "network": {"sync_relay_url": "", "host_ip": "", "router_ip": ""},
    "apps": {"default": "work"},
    "storage": {
        "work": {"provider": "local", "root": "storage/work"},
        "home": {"provider": "local", "root": "storage/home"},
        "shared": {
            "provider": "filesystem",
            "sync_provider": "google_drive",
            "mount_path": "storage/shared",
        },
    },
    "connectivity": {"poll_seconds": 20},
}

ENTRY_FIELDS: List[Dict[str, Any]] = [
    {"label": "HTTP Host", "path": ("server", "host")},
    {"label": "HTTP Port", "path": ("server", "port"), "coerce": int},
    {"label": "OpenRouter API key", "path": ("integrations", "openrouter_key")},
    {"label": "OpenRouter model", "path": ("integrations", "openrouter_model")},
    {"label": "Genesis API key", "path": ("integrations", "genesis_key")},
    {"label": "Genesis model", "path": ("integrations", "genesis_model")},
    {"label": "Default model", "path": ("integrations", "default_model")},
    {"label": "OpenAI API key", "path": ("integrations", "openai_key")},
    {"label": "OpenAI model", "path": ("integrations", "openai_model")},
    {"label": "OpenAI base URL", "path": ("integrations", "openai_base")},
    {"label": "Qwen API key", "path": ("integrations", "qwen_key")},
    {"label": "Qwen model", "path": ("integrations", "qwen_model")},
    {"label": "Qwen base URL", "path": ("integrations", "qwen_base")},
    {"label": "Ollama host", "path": ("integrations", "ollama_host")},
    {"label": "Home Ollama model", "path": ("integrations", "ollama_home_model")},
    {"label": "Genesis base URL", "path": ("integrations", "genesis_base")},
    {"label": "Vault passphrase", "path": ("security", "vault_passphrase")},
    {"label": "Vault PIN", "path": ("security", "vault_pin")},
    {"label": "Desktop export directory", "path": ("paths", "desktop_export"), "browse": "dir"},
    {"label": "Icons directory", "path": ("paths", "icons_dir"), "browse": "dir"},
    {"label": "Avatar path", "path": ("paths", "avatar_path"), "browse": "file"},
    {"label": "RAG docs directory", "path": ("paths", "rag_docs_dir"), "browse": "dir"},
    {"label": "SQLite database", "path": ("paths", "db_path"), "browse": "file"},
    {"label": "Sync relay URL", "path": ("network", "sync_relay_url")},
    {"label": "Host IP", "path": ("network", "host_ip")},
    {"label": "Router IP", "path": ("network", "router_ip")},
    {"label": "Default cockpit (work/home/m)", "path": ("apps", "default")},
    {"label": "Connectivity poll (s)", "path": ("connectivity", "poll_seconds"), "coerce": int},
    {"label": "Work storage root", "path": ("storage", "work", "root"), "browse": "dir"},
    {"label": "Shared storage mount", "path": ("storage", "shared", "mount_path"), "browse": "dir"},
    {"label": "Home storage root", "path": ("storage", "home", "root"), "browse": "dir"},
    {"label": "Shared storage provider", "path": ("storage", "shared", "provider")},
    {"label": "Shared sync provider", "path": ("storage", "shared", "sync_provider")},
]

TOGGLE_FIELDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("Work MONKY", ("features", "work")),
    ("Home MONKY", ("features", "home")),
    ("Mobile MONKY", ("features", "mobile")),
    ("Assistant embeddings", ("features", "assistant_embeddings")),
]


def deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *override* into *base*."""

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def load_template_data() -> Dict[str, Any]:
    config = deepcopy(DEFAULT_TEMPLATE)
    if TEMPLATE_PATH.exists():
        try:
            data = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                deep_update(config, data)
        except Exception:
            pass
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                deep_update(config, data)
        except Exception:
            pass
    return config


def get_nested(data: Dict[str, Any], path: Iterable[str], default: Any = "") -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def set_nested(data: Dict[str, Any], path: Iterable[str], value: Any) -> None:
    path = tuple(path)
    if not path:
        return
    current = data
    for key in path[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[path[-1]] = value


from utils import get_python_executable

def launch_monky_process() -> None:
    python = get_python_executable()
    subprocess.Popen([python, str(BASE_DIR / "launch_monky.py")])


def write_config(config: Dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


class WizardApp(tk.Tk):
    def __init__(self, *, auto_launch: bool = True):
        super().__init__()
        self.title("MONKY Setup Wizard")
        self.configure(bg="#090b13")
        self.resizable(False, False)
        self.auto_launch = auto_launch
        self.template = load_template_data()
        self.entries: Dict[Tuple[str, ...], tk.Entry] = {}
        self.entry_meta: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        self.checks: Dict[Tuple[str, ...], tk.BooleanVar] = {}
        self.create_widgets()

    def create_widgets(self) -> None:
        title = tk.Label(
            self,
            text="Welcome to MONKY",
            font=("Segoe UI", 16, "bold"),
            fg="#58f7ff",
            bg="#090b13",
        )
        title.grid(row=0, column=0, columnspan=3, padx=24, pady=(20, 8))
        subtitle = tk.Label(
            self,
            text="Configure local keys, storage paths, and cockpit toggles",
            font=("Segoe UI", 10),
            fg="#b7c1e2",
            bg="#090b13",
        )
        subtitle.grid(row=1, column=0, columnspan=3, padx=24, pady=(0, 20))

        row = 2

        for field in ENTRY_FIELDS:
            label = field["label"]
            path = tuple(field["path"])
            browse = field.get("browse")
            current = get_nested(self.template, path, "")
            placeholder = "" if current is None else str(current)

            tk.Label(self, text=label, fg="#f5f8ff", bg="#090b13", anchor="w").grid(
                row=row, column=0, sticky="w", padx=(24, 12), pady=6
            )
            entry = tk.Entry(self, width=38, fg="#05060a", bg="#e2e8ff")
            entry.insert(0, placeholder)
            entry.grid(row=row, column=1, padx=(0, 12), pady=6, sticky="we")
            self.entries[path] = entry
            self.entry_meta[path] = field

            if browse:
                btn = tk.Button(self, text="Browse", command=lambda e=entry, m=browse: self.browse_path(e, m))
                btn.grid(row=row, column=2, padx=(0, 24), pady=6)
            else:
                spacer = tk.Label(self, text="", bg="#090b13")
                spacer.grid(row=row, column=2, padx=(0, 24), pady=6)
            row += 1

        tk.Label(self, text="Enable apps", fg="#f5f8ff", bg="#090b13", anchor="w").grid(
            row=row, column=0, padx=(24, 12), pady=(18, 6), sticky="w"
        )
        row += 1

        for label, path in TOGGLE_FIELDS:
            value = bool(get_nested(self.template, path, True))
            var = tk.BooleanVar(value=value)
            chk = tk.Checkbutton(
                self,
                text=label,
                variable=var,
                fg="#d2dcff",
                bg="#090b13",
                activebackground="#090b13",
                selectcolor="#141a2a",
            )
            chk.grid(row=row, column=0, columnspan=2, padx=(24, 12), sticky="w")
            self.checks[tuple(path)] = var
            row += 1

        footer = tk.Frame(self, bg="#090b13")
        footer.grid(row=row, column=0, columnspan=3, pady=(24, 20))
        tk.Button(footer, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=6)
        tk.Button(footer, text="Save & Launch", command=self.save_config).pack(side=tk.RIGHT, padx=6)

    def browse_path(self, entry: tk.Entry, mode: str) -> None:
        if mode == "dir":
            path = filedialog.askdirectory()
        else:
            path = filedialog.askopenfilename()
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)

    def save_config(self) -> None:
        config = self.template
        for path, entry in self.entries.items():
            field = self.entry_meta[path]
            raw = entry.get().strip()
            coerce = field.get("coerce")

            if coerce is int:
                if not raw:
                    continue  # keep previous value
                try:
                    value = int(raw)
                except ValueError:
                    messagebox.showerror("Invalid value", f"{field['label']} must be an integer")
                    return
            else:
                value = raw

            set_nested(config, path, value)

        for path, var in self.checks.items():
            set_nested(config, path, bool(var.get()))

        write_config(config)
        messagebox.showinfo("Saved", f"Configuration saved to {CONFIG_PATH}")

        if self.auto_launch:
            self.after(300, self.launch_monky)
        else:
            self.after(100, self.destroy)

    def launch_monky(self) -> None:
        if not self.auto_launch:
            return

        def _launch():
            try:
                launch_monky_process()
            except Exception as exc:  # pragma: no cover - GUI fallback
                self.after(0, lambda: messagebox.showerror("Launch failed", str(exc)))

        threading.Thread(target=_launch, daemon=True).start()
        self.after(200, self.destroy)


def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{text}{suffix}: ")
    except EOFError:
        value = ""
    value = value.strip()
    return value if value else default


def prompt_bool(text: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    try:
        value = input(f"{text} [{suffix}]: ").strip().lower()
    except EOFError:
        value = ""
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


def run_cli_wizard(*, auto_launch: bool) -> None:
    print("MONKY Setup Wizard (CLI)")
    print("Press Enter to keep the value shown in brackets.")

    config = load_template_data()

    for field in ENTRY_FIELDS:
        path = tuple(field["path"])
        current = get_nested(config, path, "")
        default = "" if current is None else str(current)
        value = prompt(field["label"], default)
        if not value:
            continue
        if field.get("coerce") is int:
            try:
                value_int = int(value)
            except ValueError:
                print(f"{field['label']} must be an integer. Value unchanged.")
                continue
            set_nested(config, path, value_int)
        else:
            set_nested(config, path, value)

    for label, path in TOGGLE_FIELDS:
        current = bool(get_nested(config, path, True))
        result = prompt_bool(label, current)
        set_nested(config, path, result)

    write_config(config)
    print(f"Configuration saved to {CONFIG_PATH}")

    if auto_launch:
        try:
            launch_monky_process()
            print("Launcher starting…")
        except Exception as exc:  # pragma: no cover - CLI fallback
            print(f"Launch failed: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MONKY setup wizard")
    parser.add_argument("--cli", action="store_true", help="Force interactive CLI mode")
    parser.add_argument("--no-launch", action="store_true", help="Do not launch MONKY after saving")
    args = parser.parse_args()

    auto_launch = not args.no_launch
    headless = sys.platform.startswith("linux") and not os.environ.get("DISPLAY")

    if args.cli or headless:
        if headless and not args.cli:
            print("No display detected; running CLI wizard.")
        try:
            run_cli_wizard(auto_launch=auto_launch)
        except KeyboardInterrupt:
            print("\nSetup cancelled by user.")
        return

    app = WizardApp(auto_launch=auto_launch)
    app.mainloop()


if __name__ == "__main__":
    main()
