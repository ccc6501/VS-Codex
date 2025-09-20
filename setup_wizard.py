import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATE_PATH = BASE_DIR / "config_template.json"


class WizardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MONKY Setup Wizard")
        self.configure(bg="#090b13")
        self.resizable(False, False)
        self.template = self.load_template()
        self.entries = {}
        self.checks = {}
        self.create_widgets()

    def load_template(self):
        if TEMPLATE_PATH.exists():
            with TEMPLATE_PATH.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        # sensible defaults if template missing
        return {
            "server": {"host": "127.0.0.1", "port": 5050},
            "features": {
                "work": True,
                "home": True,
                "mobile": True,
                "sensor_simulation": True,
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
                "genesis_key": "",
                "default_model": "monky-local",
            },
            "security": {"vault_passphrase": "", "vault_pin": "1234"},
            "network": {"sync_relay_url": ""},
            "apps": {"default": "work"},
        }

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
        add_entry("OpenRouter API key", ("integrations", "openrouter_key"), "")
        add_entry("Genesis API key", ("integrations", "genesis_key"), "")
        add_entry("Default model", ("integrations", "default_model"), self.template["integrations"].get("default_model", "monky-local"))
        add_entry("Vault passphrase", ("security", "vault_passphrase"), "")
        add_entry("Vault PIN", ("security", "vault_pin"), self.template["security"].get("vault_pin", "1234"))
        add_entry("Desktop export directory", ("paths", "desktop_export"), self.template["paths"].get("desktop_export", ""), browse="dir")
        add_entry("Icons directory", ("paths", "icons_dir"), self.template["paths"].get("icons_dir", ""), browse="dir")
        add_entry("Avatar path", ("paths", "avatar_path"), self.template["paths"].get("avatar_path", ""), browse="file")
        add_entry("RAG docs directory", ("paths", "rag_docs_dir"), self.template["paths"].get("rag_docs_dir", ""), browse="dir")
        add_entry("SQLite database", ("paths", "db_path"), self.template["paths"].get("db_path", "monky.db"), browse="file")
        add_entry("Sync relay URL", ("network", "sync_relay_url"), self.template["network"].get("sync_relay_url", ""))
        add_entry("Default cockpit (work/home/m)", ("apps", "default"), self.template["apps"].get("default", "work"))

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
            if leaf == "port":
                try:
                    target[leaf] = int(value)
                except ValueError:
                    messagebox.showerror("Invalid port", "Port must be an integer")
                    return
            else:
                target[leaf] = value
        for key_path, var in self.checks.items():
            target = config
            for key in key_path[:-1]:
                target = target.setdefault(key, {})
            target[key_path[-1]] = bool(var.get())

        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
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


def main():
    app = WizardApp()
    app.mainloop()


if __name__ == "__main__":
    main()
