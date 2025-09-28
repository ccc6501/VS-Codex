import atexit
import base64
import io
import json
import os
import random
import signal
import socket
import time
import sqlite3
import threading
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import pandas as pd

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - optional dependency
    Image = ImageDraw = None  # type: ignore
    _PIL_AVAILABLE = False
else:
    _PIL_AVAILABLE = True

try:
    import pystray
except Exception:  # pragma: no cover - optional dependency
    pystray = None  # type: ignore

from apscheduler.schedulers.background import BackgroundScheduler
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from flask import (Flask, abort, g, jsonify, redirect, request,
                   send_from_directory, url_for)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TEMPLATE_PATH = BASE_DIR / "config_template.json"
DEFAULT_DB_PATH = BASE_DIR / "monky.db"


def load_config() -> Dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    if TEMPLATE_PATH.exists():
        with TEMPLATE_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    raise RuntimeError("No configuration available. Run setup_wizard.py first.")


config = load_config()
DB_PATH = Path(config.get("paths", {}).get("db_path", DEFAULT_DB_PATH))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

app = Flask(__name__)
app.secret_key = os.environ.get("MONKY_SECRET", os.urandom(32))

scheduler = BackgroundScheduler(daemon=True)
_scheduler_started = False
_scheduler_lock = threading.Lock()


class ProviderError(Exception):
    def __init__(self, provider: str, detail: str, *, status_code: int = 500):
        super().__init__(detail)
        self.provider = provider
        self.detail = detail
        self.status_code = status_code


def get_integration(key: str, default: Optional[str] = None) -> Optional[str]:
    return config.get("integrations", {}).get(key, default)


def resolve_storage_path(scope: Optional[str] = None, override: Optional[str] = None) -> Path:
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else (BASE_DIR / path)

    storage_cfg = config.get("storage", {})
    scope = (scope or "work").lower()
    if scope == "home":
        root = storage_cfg.get("home", {}).get("root")
    elif scope == "shared":
        root = storage_cfg.get("shared", {}).get("mount_path")
    else:
        root = storage_cfg.get("work", {}).get("root")

    if not root:
        return BASE_DIR / "storage" / scope
    path = Path(root).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


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
    raise RuntimeError("Unable to find available port")


_tray_icon = None
_tray_thread: Optional[threading.Thread] = None


PROVIDER_DEFINITIONS = {
    "local": {
        "label": "Local Summary",
        "model": "heuristic",
    },
    "openrouter": {
        "label": "OpenRouter",
        "key": "openrouter_key",
        "model_key": "openrouter_model",
        "default_model": "openrouter/auto",
        "url": "https://openrouter.ai/api/v1/chat/completions",
    },
    "openai": {
        "label": "OpenAI",
        "key": "openai_key",
        "model_key": "openai_model",
        "default_model": "gpt-4o-mini",
        "url": None,  # base can be overridden by config
    },
    "qwen": {
        "label": "Qwen",
        "key": "qwen_key",
        "model_key": "qwen_model",
        "default_model": "qwen-turbo",
    },
    "genesis": {
        "label": "Genesis",
        "key": "genesis_key",
        "model_key": "genesis_model",
        "default_model": "llama-3.3-70b-instruct",
    },
    "ollama": {
        "label": "Ollama (local)",
        "key": "ollama_host",
        "model_key": "ollama_home_model",
        "default_model": "llama3",
    },
}


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            due_date TEXT,
            frequency TEXT,
            status TEXT DEFAULT 'scheduled',
            category TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bill_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            paid_on TEXT,
            amount REAL,
            notes TEXT,
            FOREIGN KEY (bill_id) REFERENCES bills(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            owner TEXT,
            description TEXT,
            due_date TEXT,
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'todo',
            due_date TEXT,
            priority TEXT,
            scope TEXT,
            tags TEXT,
            project_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            project_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT,
            unit TEXT,
            normal_min REAL,
            normal_max REAL,
            source TEXT,
            status TEXT,
            last_value REAL,
            last_updated TEXT,
            location TEXT,
            description TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id INTEGER NOT NULL,
            value REAL NOT NULL,
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sensor_id) REFERENCES sensors(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            source TEXT,
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS rag_docs_fts USING fts5(
            doc_id UNINDEXED,
            title,
            content,
            tokenize = 'porter'
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS data_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            tags TEXT,
            location TEXT,
            source TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS kpi_datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metrics_json TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vault_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            category TEXT,
            nonce BLOB,
            tag BLOB,
            data BLOB,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            provider TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (thread_id) REFERENCES assistant_threads(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            kind TEXT,
            title TEXT,
            detail TEXT,
            FOREIGN KEY (message_id) REFERENCES assistant_messages(id) ON DELETE CASCADE
        )
        """
    )

    conn.commit()

    _apply_migrations(conn)
    conn.commit()
    conn.close()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    def column_exists(table: str, column: str) -> bool:
        cur.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cur.fetchall())

    if not column_exists("tasks", "project_id"):
        cur.execute("ALTER TABLE tasks ADD COLUMN project_id INTEGER")

    if not column_exists("notes", "project_id"):
        cur.execute("ALTER TABLE notes ADD COLUMN project_id INTEGER")

    if not column_exists("assistant_messages", "thread_id"):
        cur.execute("ALTER TABLE assistant_messages ADD COLUMN thread_id INTEGER")

    if not column_exists("assistant_messages", "provider"):
        cur.execute("ALTER TABLE assistant_messages ADD COLUMN provider TEXT")

    # Ensure default assistant thread exists
    cur.execute("SELECT id FROM assistant_threads ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row:
        default_thread = row[0]
    else:
        cur.execute("INSERT INTO assistant_threads(title) VALUES(?)", ("MONKY Assistant",))
        default_thread = cur.lastrowid

    cur.execute("UPDATE assistant_messages SET thread_id = COALESCE(thread_id, ?) WHERE thread_id IS NULL", (default_thread,))



def _resolve_thread(conn: sqlite3.Connection, thread_id: Optional[int]):
    if thread_id is not None:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM assistant_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
        if not row:
            abort(404, description="Thread not found")
        return thread_id, row

    row = conn.execute(
        "SELECT id, title, created_at, updated_at FROM assistant_threads ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row:
        return row["id"], row

    cur = conn.cursor()
    cur.execute("INSERT INTO assistant_threads(title) VALUES(?)", ("MONKY Assistant",))
    conn.commit()
    thread_id = cur.lastrowid
    row = conn.execute(
        "SELECT id, title, created_at, updated_at FROM assistant_threads WHERE id = ?",
        (thread_id,),
    ).fetchone()
    return thread_id, row


def get_meta_value(key: str, default: str = None) -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row:
        return row["value"]
    return default


def set_meta_value(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def deep_merge(target: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def strip_masked(values: Any) -> Any:
    if isinstance(values, dict):
        for key in list(values.keys()):
            val = values[key]
            if isinstance(val, dict):
                strip_masked(val)
            elif isinstance(val, str) and val.strip() in {"••••", "***", "*** hidden ***"}:
                values.pop(key)
    return values


def sanitized_config() -> Dict[str, Any]:
    data = json.loads(json.dumps(config))  # deep copy
    integrations = data.get("integrations", {})
    for key in (
        "openrouter_key",
        "genesis_key",
        "openai_key",
        "qwen_key",
    ):
        if integrations.get(key):
            integrations[key] = "••••"
    security = data.get("security", {})
    for key in ("vault_passphrase", "vault_pin"):
        if security.get(key):
            security[key] = "••••"
    return data


DEFAULT_REQUEST_TIMEOUT = 25


def call_provider(provider: str, message: str, model: Optional[str] = None) -> Dict[str, Any]:
    provider = provider.lower()
    spec = PROVIDER_DEFINITIONS.get(provider)
    if not spec:
        raise ProviderError(provider, "Unknown provider", status_code=400)

    if provider == "local":
        reply, sources = generate_local_summary(message)
        return {"reply": reply, "sources": sources, "provider": provider, "model": spec.get("model", "heuristic")}

    integrations = config.get("integrations", {})
    model_key = spec.get("model_key")
    resolved_model = model or (integrations.get(model_key) if model_key else spec.get("default_model"))

    key_name = spec.get("key")
    credential = integrations.get(key_name) if key_name else None

    if not credential:
        raise ProviderError(provider, "Missing API credential")

    try:
        if provider == "openrouter":
            return call_openrouter(message, resolved_model, credential)
        if provider == "openai":
            base_url = integrations.get("openai_base", "https://api.openai.com/v1")
            return call_openai(message, resolved_model, credential, base_url)
        if provider == "qwen":
            base_url = integrations.get("qwen_base", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
            return call_openai(message, resolved_model, credential, base_url, api_key_header="Authorization", bearer_prefix="Bearer ")
        if provider == "genesis":
            base_url = integrations.get("genesis_base", "https://api.ai.us.lmco.com/v1")
            return call_openai(message, resolved_model, credential, base_url)
        if provider == "ollama":
            host = integrations.get("ollama_host", "http://localhost:11434").rstrip("/")
            return call_ollama(message, resolved_model, host)
    except ProviderError:
        raise
    except requests.RequestException as exc:
        detail = getattr(exc.response, "text", str(exc))
        raise ProviderError(provider, f"HTTP error: {detail}") from exc
    except Exception as exc:  # pragma: no cover - unexpected
        raise ProviderError(provider, str(exc)) from exc

    raise ProviderError(provider, "Unsupported provider")


def call_openrouter(message: str, model: str, api_key: str) -> Dict[str, Any]:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "MONKY",
    }
    payload = {
        "model": model or "openrouter/auto",
        "messages": [
            {"role": "system", "content": "You are MONKY, a helpful operations co-pilot."},
            {"role": "user", "content": message},
        ],
        "temperature": 0.4,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_REQUEST_TIMEOUT)
    if response.status_code >= 400:
        raise ProviderError("openrouter", response.text, status_code=response.status_code)
    data = response.json()
    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not reply:
        raise ProviderError("openrouter", "Empty response from OpenRouter")
    return {"reply": reply.strip(), "raw": data, "provider": "openrouter", "model": payload["model"]}


def call_openai(message: str, model: str, api_key: str, base_url: str, *, api_key_header: str = "Authorization", bearer_prefix: str = "Bearer ") -> Dict[str, Any]:
    base_url = base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        api_key_header: f"{bearer_prefix}{api_key}" if bearer_prefix else api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are MONKY, a helpful operations co-pilot."},
            {"role": "user", "content": message},
        ],
        "temperature": 0.2,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_REQUEST_TIMEOUT)
    if response.status_code >= 400:
        raise ProviderError("openai", response.text, status_code=response.status_code)
    data = response.json()
    reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not reply:
        raise ProviderError("openai", "Empty response from provider")
    return {"reply": reply.strip(), "raw": data, "provider": "openai", "model": model}


def call_ollama(message: str, model: str, host: str) -> Dict[str, Any]:
    url = f"{host}/api/chat"
    payload = {
        "model": model or "llama3",
        "messages": [
            {"role": "system", "content": "You are MONKY, a helpful operations co-pilot."},
            {"role": "user", "content": message},
        ],
        "options": {"temperature": 0.2},
        "stream": False,
    }
    response = requests.post(url, json=payload, timeout=DEFAULT_REQUEST_TIMEOUT)
    if response.status_code >= 400:
        raise ProviderError("ollama", response.text, status_code=response.status_code)
    data = response.json()
    reply = data.get("message", {}).get("content")
    if not reply and "response" in data:
        reply = data.get("response")
    if not reply:
        raise ProviderError("ollama", "Empty response from Ollama")
    return {"reply": reply.strip(), "raw": data, "provider": "ollama", "model": payload["model"]}


def ensure_meta_defaults():
    if get_meta_value("vault_salt") is None:
        salt = base64.b64encode(os.urandom(16)).decode("utf-8")
        set_meta_value("vault_salt", salt)
    if get_meta_value("vault_pin_hash") is None:
        default_pin = config.get("security", {}).get("vault_pin", "1234")
        if default_pin:
            set_meta_value("vault_pin_hash", hash_pin(default_pin))
    if get_meta_value("assistant_summary") is None:
        set_meta_value("assistant_summary", "MONKY Assistant ready.")


def seed_database():
    conn = get_connection()

    if conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO projects(name, status, owner, description, due_date, tags) VALUES(?,?,?,?,?,?)",
            [
                (
                    "Atlas Reboot",
                    "active",
                    "A. Rivera",
                    "Refresh MONKY work cockpit with neon theming and telemetry hooks.",
                    (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d"),
                    "work,priority",
                ),
                (
                    "House Systems",
                    "active",
                    "T. Nguyen",
                    "Tune smart-home schedules and budget alerts before summer.",
                    (datetime.utcnow() + timedelta(days=21)).strftime("%Y-%m-%d"),
                    "home,automation",
                ),
            ],
        )

    project_rows = conn.execute("SELECT id, name FROM projects").fetchall()
    project_lookup = {row["name"]: row["id"] for row in project_rows}

    if conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO tasks(title, description, status, due_date, priority, scope, tags, project_id) VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    "Rebaseline sprint",
                    "Align backlog with stakeholders and prepare velocity chart.",
                    "in-progress",
                    (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d"),
                    "high",
                    "Work",
                    "planning,alignment",
                    project_lookup.get("Atlas Reboot"),
                ),
                (
                    "Ship MONKY dashboard",
                    "Finalize neon theming and QA flows for launch.",
                    "todo",
                    (datetime.utcnow() + timedelta(days=5)).strftime("%Y-%m-%d"),
                    "high",
                    "Work",
                    "ui,launch",
                    project_lookup.get("Atlas Reboot"),
                ),
                (
                    "Tune HVAC schedule",
                    "Calibrate overnight cooling curve and humidity targets.",
                    "todo",
                    (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d"),
                    "medium",
                    "Home",
                    "automation,energy",
                    project_lookup.get("House Systems"),
                ),
            ],
        )

    if conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO notes(title, content, project_id) VALUES(?,?,?)",
            [
                (
                    "Q3 Planning",
                    "- Prep OKR review\n- Confirm sensor integrations\n- Validate vault encryption",
                    project_lookup.get("Atlas Reboot"),
                ),
                (
                    "Vacation Prep",
                    "Pool shock Tuesday\nUpdate neighbor contact sheet\nAuto-pay utilities",
                    project_lookup.get("House Systems"),
                ),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO bills(name, amount, due_date, frequency, status, category) VALUES(?,?,?,?,?,?)",
            [
                ("Power Utility", 120.42, (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d"), "monthly", "scheduled", "utilities"),
                ("Fiber Internet", 80.00, (datetime.utcnow() + timedelta(days=8)).strftime("%Y-%m-%d"), "monthly", "scheduled", "utilities"),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM sensors").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO sensors(name, kind, unit, normal_min, normal_max, source, status, last_value, last_updated, location, description) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "Server Room Temp",
                    "temperature",
                    "°C",
                    18,
                    26,
                    "simulated",
                    "OK",
                    22.5,
                    datetime.utcnow().isoformat(),
                    "Work HQ",
                    "Thermal probe feeding MONKY",
                ),
                (
                    "Pool Chemistry",
                    "ph",
                    "pH",
                    7.2,
                    7.6,
                    "simulated",
                    "OK",
                    7.4,
                    datetime.utcnow().isoformat(),
                    "Home",
                    "Simulated maintenance feed",
                ),
                (
                    "Studio Air Quality",
                    "aqi",
                    "AQI",
                    0,
                    75,
                    "simulated",
                    "OK",
                    22,
                    datetime.utcnow().isoformat(),
                    "Home",
                    "CO₂ + particulates composite",
                ),
            ],
        )
        sensors = conn.execute("SELECT id, last_value FROM sensors").fetchall()
        for sensor in sensors:
            conn.execute(
                "INSERT INTO sensor_readings(sensor_id, value, recorded_at) VALUES(?,?,?)",
                (sensor["id"], sensor["last_value"], datetime.utcnow().isoformat()),
            )
    if conn.execute("SELECT COUNT(*) FROM rag_docs").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO rag_docs(title, content, source, tags) VALUES(?,?,?,?)",
            (
                "MONKY Handbook",
                "Monitor operations, notify anomalies, keep humans in the loop.",
                "system",
                "handbook,overview",
            ),
        )
        doc_id = conn.execute("SELECT id FROM rag_docs WHERE title = ?", ("MONKY Handbook",)).fetchone()[0]
        conn.execute(
            "INSERT INTO rag_docs_fts(doc_id, title, content) VALUES(?,?,?)",
            (doc_id, "MONKY Handbook", "Monitor operations, notify anomalies, keep humans in the loop."),
        )

    if conn.execute("SELECT COUNT(*) FROM data_assets").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO data_assets(title, description, tags, location, source) VALUES(?,?,?,?,?)",
            (
                "Onboarding Playbook",
                "Reference deck for new collaborators with process diagrams and vault policy.",
                "work,process",
                "knowledgebase/onboarding.pdf",
                "local",
            ),
        )

    if conn.execute("SELECT COUNT(*) FROM kpi_datasets").fetchone()[0] == 0:
        sample_metrics = {
            "Revenue": {"sum": 128000, "avg": 10666.67, "min": 9800, "max": 14250},
            "Cycle Time": {"avg": 4.2, "min": 3.1, "max": 5.0},
        }
        conn.execute(
            "INSERT INTO kpi_datasets(name, source, metrics_json) VALUES(?,?,?)",
            ("Baseline KPIs", "system seed", json.dumps(sample_metrics)),
        )

    conn.commit()
    conn.close()


def hash_pin(pin: str) -> str:
    salt = get_meta_value("vault_salt")
    if not salt:
        ensure_meta_defaults()
        salt = get_meta_value("vault_salt")
    digest = hashes.Hash(hashes.SHA256(), backend=default_backend())
    digest.update(base64.b64decode(salt))
    digest.update(pin.encode("utf-8"))
    return base64.b64encode(digest.finalize()).decode("utf-8")


def verify_pin(pin: str) -> bool:
    stored = get_meta_value("vault_pin_hash")
    if not stored:
        return False
    return hash_pin(pin) == stored


def derive_vault_key() -> bytes:
    passphrase = config.get("security", {}).get("vault_passphrase")
    if not passphrase:
        raise RuntimeError("Vault passphrase missing in configuration.")
    salt = base64.b64decode(get_meta_value("vault_salt"))
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
        backend=default_backend(),
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_secret(plaintext: str) -> Dict[str, bytes]:
    key = derive_vault_key()
    aes = AESGCM(key)
    nonce = os.urandom(12)
    data = aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return {"nonce": nonce, "ciphertext": data[:-16], "tag": data[-16:]}


def decrypt_secret(nonce: bytes, ciphertext: bytes, tag: bytes) -> str:
    key = derive_vault_key()
    aes = AESGCM(key)
    return aes.decrypt(nonce, ciphertext + tag, None).decode("utf-8")


def enabled_apps() -> Dict[str, bool]:
    features = config.get("features", {})
    return {
        "work": bool(features.get("work", True)),
        "home": bool(features.get("home", True)),
        "mobile": bool(features.get("mobile", True)),
    }


def sensor_simulation_enabled() -> bool:
    return bool(config.get("features", {}).get("sensor_simulation", True))


def ensure_scheduler():
    global _scheduler_started
    if not sensor_simulation_enabled():
        return
    with _scheduler_lock:
        job = None
        try:
            job = scheduler.get_job("sensor-sim")
        except Exception:
            job = None
        if job is None:
            scheduler.add_job(simulate_sensors, "interval", seconds=45, id="sensor-sim", replace_existing=True)
        try:
            scheduler.resume()
        except Exception:
            pass
        if not scheduler.running:
            scheduler.start()
        _scheduler_started = True


def simulate_sensors():
    conn = get_connection()
    sensors = conn.execute("SELECT * FROM sensors").fetchall()
    for sensor in sensors:
        normal_min = sensor["normal_min"] or 0
        normal_max = sensor["normal_max"] or 100
        mid = (normal_min + normal_max) / 2.0
        spread = (normal_max - normal_min) / 6.0 if normal_max != normal_min else 5
        new_value = random.gauss(mid, spread)
        status = "OK"
        if new_value < normal_min or new_value > normal_max:
            status = "Out-of-Range"
        elif abs(new_value - mid) > (normal_max - normal_min) * 0.35:
            status = "Drift"
        conn.execute(
            "INSERT INTO sensor_readings(sensor_id, value, recorded_at) VALUES(?,?,?)",
            (sensor["id"], new_value, datetime.utcnow().isoformat()),
        )
        conn.execute(
            "UPDATE sensors SET last_value = ?, last_updated = ?, status = ? WHERE id = ?",
            (new_value, datetime.utcnow().isoformat(), status, sensor["id"]),
        )
    cutoff = datetime.utcnow() - timedelta(days=3)
    conn.execute("DELETE FROM sensor_readings WHERE recorded_at < ?", (cutoff.isoformat(),))
    conn.commit()
    conn.close()


def _serve_file_or_404(filename: str):
    path = BASE_DIR / filename
    if not path.exists():
        abort(404, description=f"Missing asset {filename}")
    return send_from_directory(BASE_DIR, filename)


@app.route("/")
@app.route("/launch")
def index():
    return _serve_file_or_404("index_selector.html")


@app.route("/work")
def work():
    if not enabled_apps()["work"]:
        abort(404)
    return _serve_file_or_404("index_work.html")


@app.route("/home")
def home():
    if not enabled_apps()["home"]:
        abort(404)
    return _serve_file_or_404("index_home.html")


@app.route("/m")
def mobile():
    if not enabled_apps()["mobile"]:
        abort(404)
    response = _serve_file_or_404("index_mobile.html")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/server")
def server_console():
    return _serve_file_or_404("index_server.html")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(BASE_DIR, "manifest.json")


@app.route("/sw.js")
def service_worker():
    response = send_from_directory(BASE_DIR, "sw.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})


@app.route("/env")
def env_flags():
    return jsonify(
        {
            "features": enabled_apps(),
            "sensor_simulation": sensor_simulation_enabled(),
            "assistant_embeddings": bool(config.get("features", {}).get("assistant_embeddings", False)),
            "has_avatar": bool(config.get("paths", {}).get("avatar_path")),
        }
    )


@app.route("/assistant/providers", methods=["GET"])
def assistant_providers():
    integrations = config.get("integrations", {})
    providers = []
    for provider_id, spec in PROVIDER_DEFINITIONS.items():
        entry = {
            "id": provider_id,
            "label": spec.get("label", provider_id.title()),
            "model": integrations.get(spec.get("model_key", ""), spec.get("default_model")),
            "has_key": True,
        }
        key_name = spec.get("key")
        if key_name:
            entry["has_key"] = bool(integrations.get(key_name))
        providers.append(entry)

    default_provider = next((p["id"] for p in providers if p["has_key"]), "local")
    return jsonify({"providers": providers, "default": default_provider})


@app.route("/assistant/threads", methods=["GET", "POST"])
def assistant_threads():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM assistant_threads ORDER BY updated_at DESC"
        ).fetchall()
        threads = []
        for row in rows:
            preview = conn.execute(
                "SELECT content FROM assistant_messages WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            item = dict(row)
            item["preview"] = preview["content"] if preview else ""
            threads.append(item)
        return jsonify(threads)

    payload = request.get_json(force=True)
    title = (payload or {}).get("title") or "New Thread"
    cur = conn.cursor()
    cur.execute("INSERT INTO assistant_threads(title) VALUES(?)", (title,))
    conn.commit()
    return jsonify({"id": cur.lastrowid, "title": title})


@app.route("/assistant/threads/<int:thread_id>", methods=["PUT", "DELETE"])
def assistant_thread_update(thread_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        title = (payload or {}).get("title")
        if not title:
            abort(400, description="Title required")
        conn.execute("UPDATE assistant_threads SET title = ?, updated_at = ? WHERE id = ?", (title, datetime.utcnow().isoformat(), thread_id))
        conn.commit()
        return jsonify({"ok": True})

    count = conn.execute("SELECT COUNT(*) AS c FROM assistant_threads").fetchone()["c"]
    if count <= 1:
        abort(400, description="Cannot delete the last remaining thread")
    conn.execute("DELETE FROM assistant_threads WHERE id = ?", (thread_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/assistant/messages", methods=["GET"])
def assistant_messages():
    thread_id = request.args.get("thread_id", type=int)
    conn = get_db()
    thread_id, thread_row = _resolve_thread(conn, thread_id)
    messages = conn.execute(
        "SELECT id, role, content, provider, created_at FROM assistant_messages WHERE thread_id = ? ORDER BY id ASC",
        (thread_id,),
    ).fetchall()

    ids = [row["id"] for row in messages]
    sources_map = {mid: [] for mid in ids}
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        source_rows = conn.execute(
            f"SELECT message_id, kind, title, detail FROM assistant_sources WHERE message_id IN ({placeholders})",
            ids,
        ).fetchall()
        for src in source_rows:
            sources_map[src["message_id"]].append(
                {"kind": src["kind"], "title": src["title"], "detail": src["detail"]}
            )

    payload = {
        "thread": {"id": thread_row["id"], "title": thread_row["title"]},
        "messages": [
            {
                **dict(row),
                "sources": sources_map.get(row["id"], []),
            }
            for row in messages
        ],
    }
    return jsonify(payload)


@app.route("/assistant/send", methods=["POST"])
def assistant_send():
    payload = request.get_json(force=True)
    message = (payload or {}).get("message", "").strip()
    if not message:
        abort(400, description="Message required")

    provider = (payload or {}).get("provider", "local").lower() or "local"
    model = payload.get("model")
    thread_id = payload.get("thread_id")

    conn = get_db()
    thread_id, _ = _resolve_thread(conn, thread_id)
    now = datetime.utcnow().isoformat()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO assistant_messages(thread_id, role, content, provider, created_at) VALUES(?,?,?,?,?)",
        (thread_id, "user", message, provider, now),
    )

    try:
        result = call_provider(provider, message, model)
        reply = result.get("reply", "")
        sources = result.get("sources", [])
        provider_used = result.get("provider", provider)
    except ProviderError as exc:
        provider_used = "local"
        fallback_reply, fallback_sources = generate_local_summary(message)
        reply = f"[{exc.provider} unavailable] {exc.detail}\n\n{fallback_reply}"
        sources = [
            {
                "kind": "notice",
                "title": f"Fallback from {exc.provider}",
                "detail": exc.detail,
            }
        ] + fallback_sources

    cur.execute(
        "INSERT INTO assistant_messages(thread_id, role, content, provider, created_at) VALUES(?,?,?,?,?)",
        (thread_id, "assistant", reply, provider_used, datetime.utcnow().isoformat()),
    )
    assistant_message_id = cur.lastrowid
    for src in sources:
        cur.execute(
            "INSERT INTO assistant_sources(message_id, kind, title, detail) VALUES(?,?,?,?)",
            (
                assistant_message_id,
                src.get("kind"),
                src.get("title"),
                src.get("detail"),
            ),
        )

    cur.execute(
        "UPDATE assistant_threads SET updated_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), thread_id),
    )
    conn.commit()

    return jsonify({
        "reply": reply,
        "sources": sources,
        "thread_id": thread_id,
        "message_id": assistant_message_id,
        "provider": provider_used,
    })


@app.route("/assistant/ping/<string:provider>")
def assistant_ping(provider: str):
    start = time.perf_counter()
    try:
        result = call_provider(provider, "MONKY readiness ping.")
        ok = True
        detail = result.get("reply", "")[:180]
    except ProviderError as exc:
        ok = False
        detail = exc.detail
    except Exception as exc:  # pragma: no cover - unexpected
        ok = False
        detail = str(exc)
    latency_ms = round((time.perf_counter() - start) * 1000)
    status = 200 if ok else 502
    return jsonify({"provider": provider, "ok": ok, "detail": detail, "latency_ms": latency_ms}), status


@app.route("/assistant/ping", methods=["GET"])
def assistant_ping_all():
    results = []
    for provider in PROVIDER_DEFINITIONS.keys():
        start = time.perf_counter()
        try:
            result = call_provider(provider, "MONKY readiness ping.")
            ok = True
            detail = result.get("reply", "")[:180]
        except ProviderError as exc:
            ok = False
            detail = exc.detail
        except Exception as exc:  # pragma: no cover
            ok = False
            detail = str(exc)
        latency_ms = round((time.perf_counter() - start) * 1000)
        results.append({"provider": provider, "ok": ok, "detail": detail, "latency_ms": latency_ms})
    return jsonify({"results": results})


@app.route("/assistant/test", methods=["POST"])
def assistant_test():
    payload = request.get_json(force=True)
    message = (payload or {}).get("message", "").strip()
    if not message:
        abort(400, description="Message required")
    provider = (payload or {}).get("provider", "local").lower() or "local"
    model = payload.get("model")
    try:
        result = call_provider(provider, message, model)
        return jsonify(result)
    except ProviderError as exc:
        return jsonify({"error": exc.detail, "provider": exc.provider}), exc.status_code


def generate_local_summary(message: str):
    keywords = {token.lower().strip(".,!?") for token in message.split() if len(token) > 3}
    conn = get_connection()
    sources: List[Dict] = []
    summaries: List[str] = []

    # Project snapshot
    project_rows = conn.execute(
        "SELECT id, name, status, due_date FROM projects ORDER BY (due_date IS NULL), due_date ASC"
    ).fetchall()
    if project_rows:
        active = [p for p in project_rows if (p["status"] or "active").lower() != "archived"]
        if active:
            top = active[:2]
            line = "Projects: " + ", ".join(
                f"{item['name']} ({item['status']})" + (f" ▸ due {item['due_date']}" if item['due_date'] else "")
                for item in top
            )
            summaries.append(line)
        for proj in project_rows:
            haystack = (proj["name"] or "").lower()
            if keywords and any(k in haystack for k in keywords):
                sources.append(
                    {
                        "kind": "project",
                        "title": proj["name"],
                        "detail": f"Status {proj['status']} due {proj['due_date'] or 'n/a'}",
                    }
                )

    # Tasks insight
    task_rows = conn.execute(
        "SELECT id, title, description, status, due_date, priority FROM tasks ORDER BY created_at DESC"
    ).fetchall()
    open_tasks = [task for task in task_rows if (task["status"] or "todo") != "done"]
    if open_tasks:
        next_due = open_tasks[0]["due_date"] or "n/a"
        summaries.append(f"Tasks: {len(open_tasks)} active · next due {next_due}")
    for task in task_rows:
        haystack = f"{task['title']} {task['description']}".lower()
        if keywords and any(k in haystack for k in keywords):
            sources.append(
                {
                    "kind": "task",
                    "title": task["title"],
                    "detail": f"{task['status']} · due {task['due_date'] or 'n/a'}",
                }
            )

    # Notes
    note_rows = conn.execute(
        "SELECT id, title, content, created_at FROM notes ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    if note_rows:
        summaries.append(f"Notes: latest entry '{note_rows[0]['title']}'")
    for note in note_rows:
        haystack = f"{note['title']} {note['content']}".lower()
        if keywords and any(k in haystack for k in keywords):
            sources.append(
                {
                    "kind": "note",
                    "title": note["title"],
                    "detail": note["content"][:140],
                }
            )

    # Bills and budget insight
    bill_rows = conn.execute("SELECT id, name, amount, due_date, status, category FROM bills").fetchall()
    due_soon = []
    for bill in bill_rows:
        due = bill["due_date"]
        if due:
            try:
                due_dt = datetime.fromisoformat(due)
            except ValueError:
                try:
                    due_dt = datetime.strptime(due, "%Y-%m-%d")
                except ValueError:
                    due_dt = None
        else:
            due_dt = None
        if due_dt and due_dt - datetime.now(datetime.UTC) <= timedelta(days=7) and bill["status"] != "paid":
            due_soon.append(bill)
        haystack = (bill["name"] or "").lower()
        if keywords and any(k in haystack for k in keywords):
            sources.append(
                {
                    "kind": "bill",
                    "title": bill["name"],
                    "detail": f"Due {bill['due_date']} • ${bill['amount']:.2f} ({bill['status']})",
                }
            )
    if due_soon:
        summaries.append(
            "Bills: "
            + ", ".join(f"{b['name']} {b['due_date']}" for b in due_soon[:3])
            + (" …" if len(due_soon) > 3 else "")
        )

    # KPI insights
    kpi_row = conn.execute(
        "SELECT id, name, metrics_json FROM kpi_datasets ORDER BY uploaded_at DESC LIMIT 1"
    ).fetchone()
    if kpi_row:
        try:
            metrics_payload = json.loads(kpi_row["metrics_json"])
        except Exception:
            metrics_payload = {}

        metric_section = metrics_payload.get("metrics") if isinstance(metrics_payload, dict) else {}
        if metric_section:
            first_key = next(iter(metric_section))
            summary_metric = metric_section[first_key]
            if isinstance(summary_metric, dict):
                avg_val = summary_metric.get("avg") or summary_metric.get("mean")
                if avg_val is not None:
                    summaries.append(f"KPI {first_key}: avg {round(avg_val, 2)}")
        sources.append(
            {
                "kind": "kpi",
                "title": kpi_row["name"],
                "detail": f"Columns: {', '.join(metrics_payload.get('columns', []))[:80]}",
            }
        )

    # Sensor alert insight (for Home view reuse)
    sensor_rows = conn.execute("SELECT id, name, status, last_value, unit, location FROM sensors").fetchall()
    alerts = [sensor for sensor in sensor_rows if (sensor["status"] or "").lower() not in {"ok", "normal"}]
    if alerts:
        summaries.append(
            "Sensors: "
            + ", ".join(f"{s['name']} {s['status']}" for s in alerts[:3])
            + (" …" if len(alerts) > 3 else "")
        )
        for sensor in alerts:
            sources.append(
                {
                    "kind": "sensor",
                    "title": sensor["name"],
                    "detail": f"{sensor['last_value']}{sensor['unit']} at {sensor['location']}",
                }
            )

    # RAG search augmentation
    if keywords:
        query = " OR ".join(keywords)
        rag_hits = conn.execute(
            "SELECT doc_id, title, snippet(rag_docs_fts, 1, '<b>', '</b>', '…', 20) AS excerpt FROM rag_docs_fts WHERE rag_docs_fts MATCH ? LIMIT 5",
            (query,),
        ).fetchall()
        for hit in rag_hits:
            sources.append(
                {
                    "kind": "doc",
                    "title": hit["title"],
                    "detail": hit["excerpt"],
                }
            )
        if rag_hits:
            summaries.append(f"Knowledge base surfaced {len(rag_hits)} reference(s).")

    conn.close()
    if not summaries:
        summaries.append("Standing by. No anomalies detected across projects, budgets, or sensors.")
    reply = "\n".join(f"• {line}" for line in summaries)
    return reply, sources


def tray_available() -> bool:
    if pystray is None or not _PIL_AVAILABLE:
        return False
    if os.name == "posix" and not os.environ.get("DISPLAY"):
        return False
    return True


def _tray_open(host: str, port: int, path: str) -> None:
    webbrowser.open(f"http://{host}:{port}{path}")


def stop_tray() -> None:
    global _tray_icon
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None


def _tray_quit(icon) -> None:  # pragma: no cover - UI event
    try:
        icon.stop()
    except Exception:
        pass
    shutdown_application()


def start_tray(host: str, port: int) -> None:
    global _tray_icon, _tray_thread
    if not tray_available() or _tray_icon is not None:
        return

    def _make_icon():
        base = Image.new("RGBA", (64, 64), (8, 10, 18, 255))
        draw = ImageDraw.Draw(base)
        draw.rectangle([10, 10, 54, 54], outline=(88, 247, 255, 255), width=2)
        draw.text((23, 18), "M", fill=(88, 247, 255, 255))
        return base

    menu = pystray.Menu(
        pystray.MenuItem("Open Launcher", lambda icon, item: _tray_open(host, port, "/launch")),
        pystray.MenuItem("Open Work MONKY", lambda icon, item: _tray_open(host, port, "/work")),
        pystray.MenuItem("Open Home MONKY", lambda icon, item: _tray_open(host, port, "/home")),
        pystray.MenuItem("Open Server MONKY", lambda icon, item: _tray_open(host, port, "/server")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda icon, item: _tray_quit(icon)),
    )

    _tray_icon = pystray.Icon("MONKY", _make_icon(), "MONKY", menu)

    def _run_icon():  # pragma: no cover - UI loop
        try:
            _tray_icon.run()
        except Exception:
            pass

    _tray_thread = threading.Thread(target=_run_icon, daemon=True)
    _tray_thread.start()


def shutdown_application(code: int = 0) -> None:
    stop_tray()
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass
    os._exit(code)


atexit.register(stop_tray)


@app.route("/rag/docs", methods=["GET", "POST"])
def rag_docs():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, title, content, source, tags, created_at FROM rag_docs ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    title = payload.get("title") or "Untitled"
    content = payload.get("content", "")
    source = payload.get("source", "user")
    tags = payload.get("tags", "")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rag_docs(title, content, source, tags) VALUES(?,?,?,?)",
        (title, content, source, tags),
    )
    doc_id = cur.lastrowid
    cur.execute(
        "INSERT INTO rag_docs_fts(doc_id, title, content) VALUES(?,?,?)",
        (doc_id, title, content),
    )
    conn.commit()
    return jsonify({"id": doc_id, "title": title})


@app.route("/rag/docs/<int:doc_id>", methods=["DELETE"])
def rag_delete(doc_id: int):
    conn = get_db()
    conn.execute("DELETE FROM rag_docs WHERE id = ?", (doc_id,))
    conn.execute("DELETE FROM rag_docs_fts WHERE doc_id = ?", (doc_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/rag/query", methods=["POST"])
def rag_query():
    payload = request.get_json(force=True)
    query = payload.get("query", "").strip()
    if not query:
        return jsonify([])
    conn = get_db()
    rows = conn.execute(
        "SELECT doc_id, title, snippet(rag_docs_fts, 1, '<mark>', '</mark>', '…', 15) AS excerpt FROM rag_docs_fts WHERE rag_docs_fts MATCH ? LIMIT 10",
        (query,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/data/assets", methods=["GET", "POST"])
def data_assets_endpoint():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, title, description, tags, location, source, added_at FROM data_assets ORDER BY added_at DESC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    payload = request.get_json(force=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO data_assets(title, description, tags, location, source, added_at) VALUES(?,?,?,?,?,?)",
        (
            payload.get("title") or "Untitled Asset",
            payload.get("description"),
            payload.get("tags"),
            payload.get("location"),
            payload.get("source", "user"),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/data/assets/<int:asset_id>", methods=["PUT", "DELETE"])
def data_asset_update(asset_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        conn.execute(
            "UPDATE data_assets SET title=?, description=?, tags=?, location=?, source=? WHERE id=?",
            (
                payload.get("title"),
                payload.get("description"),
                payload.get("tags"),
                payload.get("location"),
                payload.get("source"),
                asset_id,
            ),
        )
        conn.commit()
        return jsonify({"ok": True})

    conn.execute("DELETE FROM data_assets WHERE id = ?", (asset_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/projects", methods=["GET", "POST"])
def projects_endpoint():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            """
            SELECT p.*, (
                SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status != 'done'
            ) AS open_tasks,
            (
                SELECT COUNT(*) FROM notes n WHERE n.project_id = p.id
            ) AS note_count
            FROM projects p
            ORDER BY p.updated_at DESC, p.created_at DESC
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])

    payload = request.get_json(force=True)
    now = datetime.utcnow().isoformat()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO projects(name, status, owner, description, due_date, tags, updated_at) VALUES(?,?,?,?,?,?,?)",
        (
            payload.get("name") or "Untitled Project",
            payload.get("status", "active"),
            payload.get("owner"),
            payload.get("description"),
            payload.get("due_date"),
            payload.get("tags"),
            now,
        ),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/projects/<int:project_id>", methods=["PUT", "DELETE"])
def projects_update(project_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            abort(404, description="Project not found")
        updates = {
            "name": payload.get("name", row["name"]),
            "status": payload.get("status", row["status"]),
            "owner": payload.get("owner", row["owner"]),
            "description": payload.get("description", row["description"]),
            "due_date": payload.get("due_date", row["due_date"]),
            "tags": payload.get("tags", row["tags"]),
        }
        conn.execute(
            """
            UPDATE projects
            SET name = ?, status = ?, owner = ?, description = ?, due_date = ?, tags = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updates["name"],
                updates["status"],
                updates["owner"],
                updates["description"],
                updates["due_date"],
                updates["tags"],
                datetime.utcnow().isoformat(),
                project_id,
            ),
        )
        conn.commit()
        return jsonify({"ok": True})

    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/kpi/datasets", methods=["GET"])
def kpi_datasets():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, source, uploaded_at, metrics_json FROM kpi_datasets ORDER BY uploaded_at DESC"
    ).fetchall()
    datasets = []
    for row in rows:
        data = dict(row)
        try:
            data["metrics"] = json.loads(data.pop("metrics_json"))
        except Exception:
            data["metrics"] = {}
        datasets.append(data)
    return jsonify(datasets)


@app.route("/kpi/upload", methods=["POST"])
def kpi_upload():
    if "file" not in request.files:
        abort(400, description="Upload requires file field")
    upload = request.files["file"]
    if upload.filename == "":
        abort(400, description="Uploaded file missing a name")

    buffer = upload.read()
    if not buffer:
        abort(400, description="Uploaded file empty")

    name = request.form.get("name") or upload.filename

    try:
        if upload.filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(buffer))
        else:
            df = pd.read_excel(io.BytesIO(buffer))
    except Exception as exc:
        abort(400, description=f"Failed to parse spreadsheet: {exc}")

    numeric = df.select_dtypes(include=["number", "float", "int"])
    metrics: Dict[str, Dict[str, float]] = {}
    for column in numeric.columns:
        series = numeric[column].dropna()
        if not len(series):
            continue
        metrics[column] = {
            "sum": float(series.sum()),
            "avg": float(series.mean()),
            "min": float(series.min()),
            "max": float(series.max()),
        }

    sample_rows = df.head(10).to_dict(orient="records")
    payload = {
        "columns": list(df.columns),
        "metrics": metrics,
        "preview": sample_rows,
        "row_count": int(len(df.index)),
    }

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO kpi_datasets(name, source, metrics_json) VALUES(?,?,?)",
        (name, upload.filename, json.dumps(payload)),
    )
    conn.commit()
    dataset_id = cur.lastrowid
    return jsonify({"id": dataset_id, "name": name, "metrics": payload})


@app.route("/kpi/datasets/<int:dataset_id>", methods=["DELETE"])
def kpi_delete(dataset_id: int):
    conn = get_db()
    conn.execute("DELETE FROM kpi_datasets WHERE id = ?", (dataset_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/bills", methods=["GET", "POST"])
def bills():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, name, amount, due_date, frequency, status, category, created_at FROM bills ORDER BY due_date"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bills(name, amount, due_date, frequency, status, category) VALUES(?,?,?,?,?,?)",
        (
            payload.get("name"),
            payload.get("amount", 0),
            payload.get("due_date"),
            payload.get("frequency"),
            payload.get("status", "scheduled"),
            payload.get("category"),
        ),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/bills/<int:bill_id>", methods=["PUT", "DELETE"])
def bill_update(bill_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        conn.execute(
            "UPDATE bills SET name=?, amount=?, due_date=?, frequency=?, status=?, category=? WHERE id=?",
            (
                payload.get("name"),
                payload.get("amount"),
                payload.get("due_date"),
                payload.get("frequency"),
                payload.get("status"),
                payload.get("category"),
                bill_id,
            ),
        )
        conn.commit()
        return jsonify({"ok": True})
    conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
    conn.execute("DELETE FROM bill_ledger WHERE bill_id = ?", (bill_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/bills/<int:bill_id>/ledger", methods=["GET", "POST"])
def bill_ledger(bill_id: int):
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, bill_id, paid_on, amount, notes FROM bill_ledger WHERE bill_id = ? ORDER BY paid_on DESC",
            (bill_id,),
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    conn.execute(
        "INSERT INTO bill_ledger(bill_id, paid_on, amount, notes) VALUES(?,?,?,?)",
        (bill_id, payload.get("paid_on"), payload.get("amount"), payload.get("notes")),
    )
    conn.commit()
    return jsonify({"ok": True})


@app.route("/budget/summary")
def budget_summary():
    conn = get_db()
    today = datetime.utcnow().date()
    upcoming = conn.execute(
        """
        SELECT id, name, amount, due_date, category, status
        FROM bills
        WHERE status != 'paid'
        ORDER BY due_date ASC
        LIMIT 8
        """
    ).fetchall()

    month_start = today.replace(day=1)
    ledger = conn.execute(
        "SELECT paid_on, amount, notes FROM bill_ledger WHERE paid_on >= ? ORDER BY paid_on DESC",
        (month_start.isoformat(),),
    ).fetchall()

    totals = conn.execute(
        "SELECT SUM(amount) as total, COUNT(*) as count FROM bills WHERE status != 'paid'"
    ).fetchone()

    due_next_week = conn.execute(
        "SELECT SUM(amount) as total FROM bills WHERE status != 'paid' AND due_date <= ?",
        ((today + timedelta(days=7)).isoformat(),),
    ).fetchone()

    categories = conn.execute(
        "SELECT category, SUM(amount) AS total FROM bills GROUP BY category ORDER BY total DESC"
    ).fetchall()

    return jsonify(
        {
            "upcoming": [dict(row) for row in upcoming],
            "ledger": [dict(row) for row in ledger],
            "totals": {
                "open_amount": float(totals["total"] or 0.0),
                "open_count": int(totals["count"] or 0),
                "due_next_week": float(due_next_week["total"] or 0.0),
            },
            "categories": [dict(row) for row in categories],
        }
    )


@app.route("/budget/import", methods=["POST"])
def budget_import():
    if "file" not in request.files:
        abort(400, description="File upload required")
    upload = request.files["file"]
    if not upload.filename:
        abort(400, description="Uploaded file missing name")

    try:
        if upload.filename.lower().endswith(".csv"):
            df = pd.read_csv(upload)
        else:
            df = pd.read_excel(upload)
    except Exception as exc:
        abort(400, description=f"Failed to parse file: {exc}")

    df.columns = [str(col).strip().lower() for col in df.columns]
    mappings = {
        "name": ["name", "bill", "vendor"],
        "amount": ["amount", "value"],
        "due_date": ["due", "due_date", "date"],
        "frequency": ["frequency", "interval"],
        "status": ["status"],
        "category": ["category", "type"],
    }

    def column(name: str):
        for alias in mappings[name]:
            if alias in df.columns:
                return alias
        return None

    results = []
    conn = get_db()
    for _, row in df.iterrows():
        name_col = column("name")
        amount_col = column("amount")
        due_col = column("due_date")
        frequency_col = column("frequency")
        status_col = column("status")
        category_col = column("category")

        name = str(row.get(name_col, "")).strip()
        if not name:
            continue
        amount = float(row.get(amount_col, 0) or 0)
        due_raw = str(row.get(due_col, "")).strip() if due_col else ""
        try:
            due_date = datetime.fromisoformat(due_raw).date().isoformat() if due_raw else None
        except Exception:
            try:
                due_date = pd.to_datetime(due_raw).date().isoformat() if due_raw else None
            except Exception:
                due_date = None

        conn.execute(
            "INSERT INTO bills(name, amount, due_date, frequency, status, category) VALUES(?,?,?,?,?,?)",
            (
                name,
                amount,
                due_date,
                str(row.get(frequency_col, "")).strip() if frequency_col else None,
                str(row.get(status_col, "scheduled")).strip() if status_col else "scheduled",
                str(row.get(category_col, "")).strip() if category_col else None,
            ),
        )
        results.append({"name": name, "amount": amount, "due_date": due_date})

    conn.commit()
    return jsonify({"imported": len(results), "items": results})


@app.route("/dashboard/<scope>")
def dashboard_scope(scope: str):
    scope = scope.lower()
    if scope not in {"work", "home"}:
        abort(404)

    conn = get_db()
    summary: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "assistant_summary": get_meta_value("assistant_summary", "MONKY ready."),
    }

    summary["tasks"] = [
        dict(row)
        for row in conn.execute(
            "SELECT id, title, status, due_date, priority, project_id FROM tasks ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
    ]
    summary["notes"] = [
        dict(row)
        for row in conn.execute(
            "SELECT id, title, content, project_id, created_at FROM notes ORDER BY created_at DESC LIMIT 6"
        ).fetchall()
    ]

    if scope == "work":
        summary["projects"] = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, status, owner, due_date, tags FROM projects ORDER BY updated_at DESC LIMIT 6"
            ).fetchall()
        ]
        kpi_rows = conn.execute(
            "SELECT id, name, metrics_json, uploaded_at FROM kpi_datasets ORDER BY uploaded_at DESC LIMIT 3"
        ).fetchall()
        datasets = []
        for row in kpi_rows:
            item = dict(row)
            try:
                item["metrics"] = json.loads(item.pop("metrics_json"))
            except Exception:
                item["metrics"] = {}
            datasets.append(item)
        summary["kpi"] = datasets
        summary["data_assets"] = [
            dict(row)
            for row in conn.execute(
                "SELECT id, title, tags, added_at, location FROM data_assets ORDER BY added_at DESC LIMIT 6"
            ).fetchall()
        ]
    else:
        summary["bills"] = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, amount, due_date, status, category FROM bills ORDER BY due_date ASC LIMIT 8"
            ).fetchall()
        ]
        summary["sensors"] = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, status, last_value, unit, location, last_updated FROM sensors ORDER BY name"
            ).fetchall()
        ]

    return jsonify(summary)


@app.route("/storage/test", methods=["POST"])
def storage_test_endpoint():
    payload = request.get_json(force=True) or {}
    scope = payload.get("scope")
    override = payload.get("path")
    target = resolve_storage_path(scope, override)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        abort(400, description=f"Failed to create directory: {exc}")

    test_file = target / f".monky_touch_{int(time.time())}.txt"
    try:
        test_file.write_text("monky-ok\n", encoding="utf-8")
    except Exception as exc:
        abort(400, description=f"Write failed: {exc}")

    return jsonify({"path": str(target), "test_file": str(test_file)})


@app.route("/tasks", methods=["GET", "POST"])
def tasks_endpoint():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            """
            SELECT id, title, description, status, due_date, priority, scope, tags, project_id, created_at
            FROM tasks
            ORDER BY created_at DESC
            """
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tasks(title, description, status, due_date, priority, scope, tags, project_id) VALUES(?,?,?,?,?,?,?,?)",
        (
            payload.get("title"),
            payload.get("description"),
            payload.get("status", "todo"),
            payload.get("due_date"),
            payload.get("priority"),
            payload.get("scope"),
            payload.get("tags"),
            payload.get("project_id"),
        ),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/tasks/<int:task_id>", methods=["PUT", "DELETE"])
def tasks_update(task_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            abort(404, description="Task not found")
        updates = {
            "title": payload.get("title", row["title"]),
            "description": payload.get("description", row["description"]),
            "status": payload.get("status", row["status"]),
            "due_date": payload.get("due_date", row["due_date"]),
            "priority": payload.get("priority", row["priority"]),
            "scope": payload.get("scope", row["scope"]),
            "tags": payload.get("tags", row["tags"]),
            "project_id": payload.get("project_id", row["project_id"]),
        }
        conn.execute(
            "UPDATE tasks SET title=?, description=?, status=?, due_date=?, priority=?, scope=?, tags=?, project_id=? WHERE id=?",
            (
                updates["title"],
                updates["description"],
                updates["status"],
                updates["due_date"],
                updates["priority"],
                updates["scope"],
                updates["tags"],
                updates["project_id"],
                task_id,
            ),
        )
        conn.commit()
        return jsonify({"ok": True})
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/notes", methods=["GET", "POST"])
def notes_endpoint():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, title, content, project_id, created_at FROM notes ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notes(title, content, project_id) VALUES(?, ?, ?)",
        (payload.get("title"), payload.get("content"), payload.get("project_id")),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/notes/<int:note_id>", methods=["PUT", "DELETE"])
def notes_update(note_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            abort(404, description="Note not found")
        updates = {
            "title": payload.get("title", row["title"]),
            "content": payload.get("content", row["content"]),
            "project_id": payload.get("project_id", row["project_id"]),
        }
        conn.execute(
            "UPDATE notes SET title=?, content=?, project_id=? WHERE id=?",
            (updates["title"], updates["content"], updates["project_id"], note_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.route("/sensors", methods=["GET"])
def sensors_endpoint():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, kind, unit, normal_min, normal_max, source, status, last_value, last_updated, location, description FROM sensors ORDER BY id"
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/sensors/<int:sensor_id>/readings", methods=["GET"])
def sensor_readings(sensor_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT value, recorded_at FROM sensor_readings WHERE sensor_id = ? ORDER BY recorded_at DESC LIMIT 96",
        (sensor_id,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/sensors/<int:sensor_id>", methods=["PUT"])
def sensor_update(sensor_id: int):
    conn = get_db()
    payload = request.get_json(force=True)
    conn.execute(
        "UPDATE sensors SET name=?, kind=?, unit=?, normal_min=?, normal_max=?, source=?, status=?, location=?, description=? WHERE id=?",
        (
            payload.get("name"),
            payload.get("kind"),
            payload.get("unit"),
            payload.get("normal_min"),
            payload.get("normal_max"),
            payload.get("source"),
            payload.get("status"),
            payload.get("location"),
            payload.get("description"),
            sensor_id,
        ),
    )
    conn.commit()
    return jsonify({"ok": True})


@app.route("/vault/auth", methods=["POST"])
def vault_auth():
    payload = request.get_json(force=True)
    pin = payload.get("pin", "")
    if not pin:
        abort(400, description="PIN required")
    if payload.get("set") and get_meta_value("vault_pin_hash") is None:
        set_meta_value("vault_pin_hash", hash_pin(pin))
        return jsonify({"ok": True, "setup": True})
    return jsonify({"ok": verify_pin(pin)})


def _vault_items_raw():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, category, nonce, tag, data, created_at, updated_at FROM vault_items ORDER BY created_at DESC"
    ).fetchall()
    return rows


@app.route("/vault/items/list", methods=["POST"])
def vault_list():
    payload = request.get_json(force=True)
    pin = payload.get("pin", "")
    if not verify_pin(pin):
        abort(403)
    items = []
    for row in _vault_items_raw():
        try:
            secret = decrypt_secret(row["nonce"], row["data"], row["tag"])
        except Exception:
            secret = "[decryption error]"
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "secret": secret,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
    return jsonify(items)


@app.route("/vault/items", methods=["POST"])
def vault_create():
    payload = request.get_json(force=True)
    pin = payload.get("pin", "")
    if not verify_pin(pin):
        abort(403)
    name = payload.get("name")
    category = payload.get("category")
    secret = payload.get("secret", "")
    encrypted = encrypt_secret(secret)
    conn = get_db()
    conn.execute(
        "INSERT INTO vault_items(name, category, nonce, tag, data) VALUES(?,?,?,?,?)",
        (
            name,
            category,
            encrypted["nonce"],
            encrypted["tag"],
            encrypted["ciphertext"],
        ),
    )
    conn.commit()
    return jsonify({"ok": True})


@app.route("/vault/items/<int:item_id>", methods=["PUT", "DELETE"])
def vault_update(item_id: int):
    payload = request.get_json(force=True)
    pin = payload.get("pin", "")
    if not verify_pin(pin):
        abort(403)
    conn = get_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM vault_items WHERE id = ?", (item_id,))
        conn.commit()
        return jsonify({"ok": True})
    name = payload.get("name")
    category = payload.get("category")
    secret = payload.get("secret", "")
    encrypted = encrypt_secret(secret)
    conn.execute(
        "UPDATE vault_items SET name=?, category=?, nonce=?, tag=?, data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            name,
            category,
            encrypted["nonce"],
            encrypted["tag"],
            encrypted["ciphertext"],
            item_id,
        ),
    )
    conn.commit()
    return jsonify({"ok": True})


@app.route("/settings/config", methods=["GET", "POST"])
def settings_config():
    if request.method == "GET":
        return jsonify(sanitized_config())

    payload = request.get_json(force=True) or {}
    if not isinstance(payload, dict):
        abort(400, description="Invalid configuration payload")
    updates = strip_masked(json.loads(json.dumps(payload)))
    deep_merge(config, updates)
    try:
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception as exc:
        abort(500, description=str(exc))
    return jsonify({"config": sanitized_config()})


@app.route("/dev/status")
def dev_status():
    conn = get_db()
    sensor_count = conn.execute("SELECT COUNT(*) FROM sensors").fetchone()[0]
    task_count = conn.execute("SELECT COUNT(*) FROM tasks WHERE status != 'done'").fetchone()[0]
    bill_count = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
    latest_backup = None
    backup_dir = BASE_DIR / "backups"
    if backup_dir.exists():
        backups = sorted(backup_dir.glob("monky-*.db"), reverse=True)
        if backups:
            latest_backup = backups[0].name
    return jsonify(
        {
            "sensor_count": sensor_count,
            "open_tasks": task_count,
            "bill_count": bill_count,
            "latest_backup": latest_backup,
            "db_path": str(DB_PATH),
            "scheduler_running": bool(_scheduler_started),
            "storage": {
                "work": str(resolve_storage_path("work")),
                "home": str(resolve_storage_path("home")),
                "shared": str(resolve_storage_path("shared")),
            },
        }
    )


@app.route("/dev/backup", methods=["POST"])
def dev_backup():
    backup_dir = BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"monky-{timestamp}.db"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return jsonify({"backup": target.name})


@app.route("/dev/restore", methods=["POST"])
def dev_restore():
    payload = request.get_json(force=True)
    name = payload.get("backup")
    backup_dir = BASE_DIR / "backups"
    target = backup_dir / name
    if not target.exists():
        abort(404, description="Backup not found")
    with sqlite3.connect(target) as src, sqlite3.connect(DB_PATH) as dst:
        src.backup(dst)
    return jsonify({"ok": True})


@app.route("/dev/ping", methods=["POST"])
def dev_ping():
    payload = request.get_json(force=True)
    url = payload.get("url")
    if not url:
        abort(400, description="URL required")
    import requests

    try:
        response = requests.get(url, timeout=5)
        return jsonify({"status": response.status_code})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/dev/toggles", methods=["POST"])
def dev_toggles():
    payload = request.get_json(force=True)
    if "sensor_simulation" in payload:
        config.setdefault("features", {})["sensor_simulation"] = bool(payload["sensor_simulation"])
        if payload["sensor_simulation"]:
            ensure_scheduler()
        else:
            with _scheduler_lock:
                try:
                    scheduler.remove_job("sensor-sim")
                except Exception:
                    pass
            config["features"]["sensor_simulation"] = False
            if scheduler.running:
                scheduler.pause()
    try:
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception:
        pass
    return jsonify({"features": config.get("features")})


@app.route("/meta/summary", methods=["GET", "POST"])
def meta_summary():
    if request.method == "GET":
        return jsonify({"summary": get_meta_value("assistant_summary", "MONKY ready.")})
    payload = request.get_json(force=True)
    set_meta_value("assistant_summary", payload.get("summary", ""))
    return jsonify({"ok": True})


def bootstrap():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    ensure_tables()
    ensure_meta_defaults()
    seed_database()
    ensure_scheduler()


bootstrap()


if __name__ == "__main__":
    host = config.get("server", {}).get("host", "127.0.0.1")
    desired_port = int(config.get("server", {}).get("port", 5050))
    try:
        port = find_available_port(host, desired_port)
        if port != desired_port:
            print(f"[MONKY] Port {desired_port} unavailable; hopped to {port}")
            config.setdefault("server", {})["port"] = port
    except Exception as exc:
        print(f"[MONKY] Failed to find available port: {exc}")
        raise

    def _handle_signal(_signum, _frame):  # pragma: no cover - system signal
        shutdown_application(0)

    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except Exception:
        pass

    start_tray(host, port)

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        shutdown_application(0)
