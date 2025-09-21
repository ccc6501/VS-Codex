import argparse
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATE_PATH = BASE_DIR / "config_template.json"
ENV_PATH = BASE_DIR / ".env"


def load_template_data() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if TEMPLATE_PATH.exists():
        try:
            with TEMPLATE_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = {}

    def ensure(path, value):
        target = data
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target.setdefault(path[-1], value)

    ensure(["server", "host"], "127.0.0.1")
    ensure(["server", "port"], 8050)
    ensure(["features"], {
        "work": True,
        "home": True,
        "mobile": True,
        "sensor_simulation": True,
        "assistant_embeddings": False,
    })
    ensure(["paths"], {
        "desktop_export": "",
        "icons_dir": "",
        "avatar_path": "",
        "rag_docs_dir": "",
        "db_path": "monky.db",
    })
    integrations = data.setdefault("integrations", {})
    integrations.setdefault("openrouter_key", "")
    integrations.setdefault("genesis_key", "")
    integrations.setdefault("default_model", "monky-local")
    integrations.setdefault("openai_key", "")
    integrations.setdefault("openai_model", "gpt-4o-mini")
    integrations.setdefault("openai_base", "https://api.openai.com/v1")
    integrations.setdefault("qwen_key", "")
    integrations.setdefault("qwen_model", "qwen-turbo")
    integrations.setdefault("qwen_base", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    integrations.setdefault("ollama_host", "http://localhost:11434")
    integrations.setdefault("ollama_home_model", "llama3")
    integrations.setdefault("genesis_base", "https://api.ai.us.lmco.com/v1")
    ensure(["security", "vault_passphrase"], "")
    ensure(["security", "vault_pin"], "1234")
    network = data.setdefault("network", {})
    network.setdefault("sync_relay_url", "")
    network.setdefault("host_ip", "")
    network.setdefault("router_ip", "")
    storage = data.setdefault("storage", {})
    storage.setdefault("work", {"provider": "local", "root": "storage/work"})
    storage.setdefault(
        "shared",
        {
            "provider": "filesystem",
            "sync_provider": "google_drive",
            "mount_path": "storage/shared",
        },
    )
    connectivity = data.setdefault("connectivity", {})
    connectivity.setdefault("poll_seconds", 20)
    apps = data.setdefault("apps", {})
    apps.setdefault("default", "work")
    return data


class WizardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MONKY Setup Wizard")
        self.configure(bg="#090b13")
        self.resizable(False, False)
        self.template = load_template_data()
        self.entries = {}
        self.checks = {}
        self.create_widgets()

    def create_widgets(self):
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

        def add_entry(label, key_path, placeholder="", browse=False):
            nonlocal row
            tk.Label(self, text=label, fg="#f5f8ff", bg="#090b13", anchor="w").grid(
                row=row, column=0, sticky="w", padx=(24, 12), pady=6
            )
            entry = tk.Entry(self, width=38, fg="#05060a", bg="#e2e8ff")
            entry.insert(0, placeholder)
            entry.grid(row=row, column=1, padx=(0, 12), pady=6, sticky="we")
            self.entries[key_path] = entry
            if browse:
                btn = tk.Button(self, text="Browse", command=lambda: self.browse_path(entry, browse))
                btn.grid(row=row, column=2, padx=(0, 24), pady=6)
            else:
                spacer = tk.Label(self, text="", bg="#090b13")
                spacer.grid(row=row, column=2, padx=(0, 24), pady=6)
            row += 1

        add_entry("HTTP Host", ("server", "host"), self.template["server"].get("host", "127.0.0.1"))
        add_entry("HTTP Port", ("server", "port"), str(self.template["server"].get("port", 5050)))
        add_entry("OpenRouter API key", ("integrations", "openrouter_key"), self.get_template_value(("integrations", "openrouter_key"), ""))
        add_entry("Genesis API key", ("integrations", "genesis_key"), self.get_template_value(("integrations", "genesis_key"), ""))
        add_entry("Default model", ("integrations", "default_model"), self.get_template_value(("integrations", "default_model"), "monky-local"))
        add_entry("OpenAI API key", ("integrations", "openai_key"), self.get_template_value(("integrations", "openai_key"), ""))
        add_entry("OpenAI model", ("integrations", "openai_model"), self.get_template_value(("integrations", "openai_model"), "gpt-4o-mini"))
        add_entry("OpenAI base URL", ("integrations", "openai_base"), self.get_template_value(("integrations", "openai_base"), "https://api.openai.com/v1"))
        add_entry("Qwen API key", ("integrations", "qwen_key"), self.get_template_value(("integrations", "qwen_key"), ""))
        add_entry("Qwen model", ("integrations", "qwen_model"), self.get_template_value(("integrations", "qwen_model"), "qwen-turbo"))
        add_entry("Qwen base URL", ("integrations", "qwen_base"), self.get_template_value(("integrations", "qwen_base"), "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"))
        add_entry("Ollama host", ("integrations", "ollama_host"), self.get_template_value(("integrations", "ollama_host"), "http://localhost:11434"))
        add_entry("Home Ollama model", ("integrations", "ollama_home_model"), self.get_template_value(("integrations", "ollama_home_model"), "llama3"))
        add_entry("Genesis base URL", ("integrations", "genesis_base"), self.get_template_value(("integrations", "genesis_base"), "https://api.ai.us.lmco.com/v1"))
        add_entry("Vault passphrase", ("security", "vault_passphrase"), "")
        add_entry("Vault PIN", ("security", "vault_pin"), self.template["security"].get("vault_pin", "1234"))
        add_entry("Desktop export directory", ("paths", "desktop_export"), self.template["paths"].get("desktop_export", ""), browse="dir")
        add_entry("Icons directory", ("paths", "icons_dir"), self.template["paths"].get("icons_dir", ""), browse="dir")
        add_entry("Avatar path", ("paths", "avatar_path"), self.template["paths"].get("avatar_path", ""), browse="file")
        add_entry("RAG docs directory", ("paths", "rag_docs_dir"), self.template["paths"].get("rag_docs_dir", ""), browse="dir")
        add_entry("SQLite database", ("paths", "db_path"), self.template["paths"].get("db_path", "monky.db"), browse="file")
        add_entry("Sync relay URL", ("network", "sync_relay_url"), self.get_template_value(("network", "sync_relay_url"), ""))
        add_entry("Host IP", ("network", "host_ip"), self.get_template_value(("network", "host_ip"), ""))
        add_entry("Router IP", ("network", "router_ip"), self.get_template_value(("network", "router_ip"), ""))
        add_entry("Default cockpit (work/home/m)", ("apps", "default"), self.template["apps"].get("default", "work"))
        add_entry("Work storage root", ("storage", "work", "root"), self.get_template_value(("storage", "work", "root"), "storage/work"), browse="dir")
        add_entry(
            "Shared storage mount",
            ("storage", "shared", "mount_path"),
            self.get_template_value(("storage", "shared", "mount_path"), "storage/shared"),
            browse="dir",
        )
        add_entry(
            "Shared storage provider",
            ("storage", "shared", "provider"),
            self.get_template_value(("storage", "shared", "provider"), "filesystem"),
        )
        add_entry(
            "Shared sync provider",
            ("storage", "shared", "sync_provider"),
            self.get_template_value(("storage", "shared", "sync_provider"), "google_drive"),
        )
        add_entry(
            "Connectivity poll (s)",
            ("connectivity", "poll_seconds"),
            str(self.get_template_value(("connectivity", "poll_seconds"), 20)),
        )

        tk.Label(self, text="Enable apps", fg="#f5f8ff", bg="#090b13", anchor="w").grid(
            row=row, column=0, padx=(24, 12), pady=(18, 6), sticky="w"
        )
        row += 1
        toggles = [
            ("Work MONKY", ("features", "work")),
            ("Home MONKY", ("features", "home")),
            ("Mobile MONKY", ("features", "mobile")),
            ("Sensor simulation", ("features", "sensor_simulation")),
            ("Assistant embeddings", ("features", "assistant_embeddings")),
        ]
        for label, key in toggles:
            var = tk.BooleanVar(value=self.get_template_value(key, True))
            chk = tk.Checkbutton(self, text=label, variable=var, fg="#d2dcff", bg="#090b13", activebackground="#090b13")
            chk.grid(row=row, column=0, columnspan=2, padx=(24, 12), sticky="w")
            self.checks[key] = var
            row += 1

        footer = tk.Frame(self, bg="#090b13")
        footer.grid(row=row, column=0, columnspan=3, pady=(24, 20))
        tk.Button(footer, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=6)
        tk.Button(footer, text="Save & Launch", command=self.save_config).pack(side=tk.RIGHT, padx=6)

    def browse_path(self, entry, mode):
        if mode == "dir":
            path = filedialog.askdirectory()
        else:
            path = filedialog.askopenfilename()
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)

    def get_template_value(self, key_path, default=None):
        data = self.template
        for key in key_path:
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                return default
        return data

    def save_config(self):
        config = self.template
        for key_path, entry in self.entries.items():
            value = entry.get().strip()
            target = config
            for key in key_path[:-1]:
                target = target.setdefault(key, {})
            leaf = key_path[-1]
            if leaf in {"port", "poll_seconds"}:
                try:
                    target[leaf] = int(value)
                except ValueError:
                    messagebox.showerror("Invalid value", f"{leaf.replace('_', ' ').title()} must be an integer")
                    return
            else:
                target[leaf] = value
        for key_path, var in self.checks.items():
            target = config
            for key in key_path[:-1]:
                target = target.setdefault(key, {})
            target[key_path[-1]] = bool(var.get())

        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        update_env_from_config(config)
        messagebox.showinfo("Saved", f"Configuration saved to {CONFIG_PATH}")
        self.after(300, self.launch_monky)

def launch_monky(self):
        self.destroy()
        def _launch():
            try:
                python = sys.executable
                if os.name == "nt":
                    pythonw = Path(python).with_name("pythonw.exe")
                    if pythonw.exists():
                        python = str(pythonw)
                subprocess.Popen([python, str(BASE_DIR / "launch_monky.py")])
            except Exception as exc:
                messagebox.showerror("Launch failed", str(exc))
        threading.Thread(target=_launch, daemon=True).start()


def update_env_from_config(config: Dict[str, Any]) -> None:
    env_values = _read_env()
    integrations = config.get("integrations", {})

    mapping = {
        "OPENAI_API_KEY": integrations.get("openai_key", ""),
        "OPENAI_MODEL": integrations.get("openai_model", ""),
        "OPENAI_BASE_URL": integrations.get("openai_base", ""),
        "OPENROUTER_API_KEY": integrations.get("openrouter_key", ""),
        "GENESIS_API_KEY": integrations.get("genesis_key", ""),
        "GENESIS_BASE_URL": integrations.get("genesis_base", ""),
        "QWEN_API_KEY": integrations.get("qwen_key", ""),
        "QWEN_BASE_URL": integrations.get("qwen_base", ""),
        "QWEN_MODEL": integrations.get("qwen_model", ""),
        "OLLAMA_HOST": integrations.get("ollama_host", ""),
    }

    changed = False
    for key, value in mapping.items():
        value = value or ""
        if value:
            if env_values.get(key) != value:
                env_values[key] = value
                changed = True
        elif key in env_values:
            del env_values[key]
            changed = True

    if changed or not ENV_PATH.exists():
        lines = [f"{k}={v}" for k, v in sorted(env_values.items())]
        ENV_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        striped = line.strip()
        if not striped or striped.startswith("#"):
            continue
        if "=" in striped:
            key, value = striped.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def _deep_update(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _prompt(text: str, default: str = "") -> str:
    prompt_text = f"{text}"
    if default:
        prompt_text += f" [{default}]"
    prompt_text += ": "
    try:
        value = input(prompt_text)
    except EOFError:
        value = ""
    value = value.strip()
    return value or default


def _prompt_bool(text: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    try:
        value = input(f"{text} [{suffix}]: ").strip().lower()
    except EOFError:
        value = ""
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


def run_cli_wizard(skip_launch: bool = False) -> None:
    print("MONKY Setup Wizard (CLI)")
    print("Press Enter to keep the value shown in brackets.")

    config = load_template_data()
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _deep_update(config, existing)
        except Exception:
            pass

    server = config.setdefault("server", {})
    server["host"] = _prompt("HTTP host", str(server.get("host", "127.0.0.1")))
    while True:
        port_value = _prompt("HTTP port", str(server.get("port", 5050)))
        try:
            server["port"] = int(port_value)
            break
        except ValueError:
            print("Port must be an integer.")

    integrations = config.setdefault("integrations", {})
    integrations["openrouter_key"] = _prompt("OpenRouter API key", integrations.get("openrouter_key", ""))
    integrations["genesis_key"] = _prompt("Genesis API key", integrations.get("genesis_key", ""))
    integrations["default_model"] = _prompt("Default model", integrations.get("default_model", "monky-local"))
    integrations["openai_key"] = _prompt("OpenAI API key", integrations.get("openai_key", ""))
    integrations["openai_model"] = _prompt("OpenAI model", integrations.get("openai_model", "gpt-4o-mini"))
    integrations["openai_base"] = _prompt("OpenAI base URL", integrations.get("openai_base", "https://api.openai.com/v1"))
    integrations["qwen_key"] = _prompt("Qwen API key", integrations.get("qwen_key", ""))
    integrations["qwen_model"] = _prompt("Qwen model", integrations.get("qwen_model", "qwen-turbo"))
    integrations["qwen_base"] = _prompt(
        "Qwen base URL",
        integrations.get("qwen_base", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    )
    integrations["ollama_host"] = _prompt("Ollama host", integrations.get("ollama_host", "http://localhost:11434"))
    integrations["ollama_home_model"] = _prompt("Home Ollama model", integrations.get("ollama_home_model", "llama3"))
    integrations["genesis_base"] = _prompt(
        "Genesis base URL",
        integrations.get("genesis_base", "https://api.ai.us.lmco.com/v1"),
    )

    security = config.setdefault("security", {})
    security["vault_passphrase"] = _prompt("Vault passphrase", security.get("vault_passphrase", ""))
    security["vault_pin"] = _prompt("Vault PIN", security.get("vault_pin", "1234"))

    paths = config.setdefault("paths", {})
    paths["desktop_export"] = _prompt("Desktop export directory", paths.get("desktop_export", ""))
    paths["icons_dir"] = _prompt("Icons directory", paths.get("icons_dir", ""))
    paths["avatar_path"] = _prompt("Avatar path", paths.get("avatar_path", ""))
    paths["rag_docs_dir"] = _prompt("RAG docs directory", paths.get("rag_docs_dir", ""))
    paths["db_path"] = _prompt("SQLite database", paths.get("db_path", "monky.db"))

    network = config.setdefault("network", {})
    network["sync_relay_url"] = _prompt("Sync relay URL", network.get("sync_relay_url", ""))
    network["host_ip"] = _prompt("Host IP", network.get("host_ip", ""))
    network["router_ip"] = _prompt("Router IP", network.get("router_ip", ""))

    storage = config.setdefault("storage", {})
    work_storage = storage.setdefault("work", {"provider": "local", "root": "storage/work"})
    work_storage["root"] = _prompt("Work storage root", work_storage.get("root", "storage/work"))
    shared_storage = storage.setdefault(
        "shared",
        {"provider": "filesystem", "sync_provider": "google_drive", "mount_path": "storage/shared"},
    )
    shared_storage["mount_path"] = _prompt("Shared storage mount", shared_storage.get("mount_path", "storage/shared"))
    shared_storage["provider"] = _prompt("Shared storage provider", shared_storage.get("provider", "filesystem"))
    shared_storage["sync_provider"] = _prompt(
        "Shared sync provider", shared_storage.get("sync_provider", "google_drive")
    )

    connectivity = config.setdefault("connectivity", {})
    while True:
        poll_value = _prompt("Connectivity poll (seconds)", str(connectivity.get("poll_seconds", 20)))
        try:
            connectivity["poll_seconds"] = int(poll_value)
            break
        except ValueError:
            print("Poll interval must be an integer.")

    features = config.setdefault("features", {})
    features["work"] = _prompt_bool("Enable Work MONKY", bool(features.get("work", True)))
    features["home"] = _prompt_bool("Enable Home MONKY", bool(features.get("home", True)))
    features["mobile"] = _prompt_bool("Enable Mobile MONKY", bool(features.get("mobile", True)))
    features["sensor_simulation"] = _prompt_bool(
        "Enable sensor simulation", bool(features.get("sensor_simulation", True))
    )
    features["assistant_embeddings"] = _prompt_bool(
        "Enable assistant embeddings", bool(features.get("assistant_embeddings", False))
    )

    apps = config.setdefault("apps", {})
    apps["default"] = _prompt("Default cockpit (work/home/m)", apps.get("default", "work"))

    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    update_env_from_config(config)
    print(f"Configuration saved to {CONFIG_PATH}")

    if skip_launch:
        return
    if _prompt_bool("Launch MONKY now?", True):
        python = sys.executable
        if os.name == "nt":
            pythonw = Path(python).with_name("pythonw.exe")
            if pythonw.exists():
                python = str(pythonw)
        subprocess.Popen([python, str(BASE_DIR / "launch_monky.py")])
        print("Launcher starting…")
    else:
        print("You can launch later with 'python launch_monky.py'.")


def main():
    parser = argparse.ArgumentParser(description="MONKY setup wizard")
    parser.add_argument("--cli", action="store_true", help="Force interactive CLI mode")
    parser.add_argument("--no-launch", action="store_true", help="Do not launch MONKY after saving")
    args = parser.parse_args()

    headless = sys.platform.startswith("linux") and not os.environ.get("DISPLAY")
    if args.cli or headless:
        if headless and not args.cli:
            print("No display detected; running CLI wizard.")
        run_cli_wizard(skip_launch=args.no_launch)
        return

    app = WizardApp()
    if args.no_launch:
        os.environ["MONKY_SKIP_AUTOLAUNCH"] = "1"
    app.mainloop()


if __name__ == "__main__":
    main()
