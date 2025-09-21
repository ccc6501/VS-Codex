import base64
import json
import os
import random
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

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
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'todo',
            due_date TEXT,
            priority TEXT,
            scope TEXT,
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


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
    if conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO tasks(title, description, status, due_date, priority, scope, tags) VALUES(?,?,?,?,?,?,?)",
            [
                (
                    "Rebaseline sprint",
                    "Align backlog with stakeholders and prepare velocity chart.",
                    "in-progress",
                    (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d"),
                    "high",
                    "Work",
                    "planning,alignment",
                ),
                (
                    "Ship MONKY dashboard",
                    "Finalize neon theming and QA flows for launch.",
                    "todo",
                    (datetime.utcnow() + timedelta(days=5)).strftime("%Y-%m-%d"),
                    "high",
                    "Work",
                    "ui,launch",
                ),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO notes(title, content) VALUES(?, ?)",
            (
                "Q3 Planning",
                "- Prep OKR review\n- Confirm sensor integrations\n- Validate vault encryption",
            ),
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
            "INSERT INTO rag_docs(title, content, source) VALUES(?,?,?)",
            (
                "MONKY Handbook",
                "Monitor operations, notify anomalies, keep humans in the loop.",
                "system",
            ),
        )
        doc_id = conn.execute("SELECT id FROM rag_docs WHERE title = ?", ("MONKY Handbook",)).fetchone()[0]
        conn.execute(
            "INSERT INTO rag_docs_fts(doc_id, title, content) VALUES(?,?,?)",
            (doc_id, "MONKY Handbook", "Monitor operations, notify anomalies, keep humans in the loop."),
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


@app.route("/")
def index():
    apps = [name for name, flag in enabled_apps().items() if flag]
    if not apps:
        return jsonify({"error": "No MONKY apps enabled"}), 503
    if len(apps) == 1:
        target = apps[0]
        if target == "mobile":
            return redirect(url_for("mobile"))
        return redirect(url_for(target))
    return send_from_directory(BASE_DIR, "index_selector.html") if (BASE_DIR / "index_selector.html").exists() else (
        """
        <html><head><title>MONKY Selector</title><style>body{background:#05060a;color:#f5f8ff;font-family:'Inter',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}a{color:#58f7ff;text-decoration:none;font-size:1.5rem;margin:1rem;padding:1.5rem 2.5rem;border:1px solid #1f2230;border-radius:18px;background:rgba(22,24,36,0.8);box-shadow:0 18px 45px rgba(0,0,0,0.45);}a:hover{background:#13141f;color:#8df7ff;}</style></head><body><div>
            <h1>MONKY Launchpad</h1>
            <div style="display:flex;flex-wrap:wrap;justify-content:center;">
                <a href="/work">Work MONKY</a>
                <a href="/home">Home MONKY</a>
                <a href="/m">Mobile MONKY</a>
            </div></div></body></html>
        """
    )


@app.route("/work")
def work():
    if not enabled_apps()["work"]:
        abort(404)
    return send_from_directory(BASE_DIR, "index_work.html")


@app.route("/home")
def home():
    if not enabled_apps()["home"]:
        abort(404)
    return send_from_directory(BASE_DIR, "index_home.html")


@app.route("/m")
def mobile():
    if not enabled_apps()["mobile"]:
        abort(404)
    response = send_from_directory(BASE_DIR, "index_mobile.html")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


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


@app.route("/assistant/messages", methods=["GET"])
def assistant_messages():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, role, content, created_at FROM assistant_messages ORDER BY id ASC"
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/assistant/send", methods=["POST"])
def assistant_send():
    payload = request.get_json(force=True)
    message = (payload or {}).get("message", "").strip()
    if not message:
        abort(400, description="Message required")
    conn = get_db()
    conn.execute(
        "INSERT INTO assistant_messages(role, content) VALUES(?, ?)",
        ("user", message),
    )
    conn.commit()
    reply, sources = generate_assistant_reply(message)
    conn.execute(
        "INSERT INTO assistant_messages(role, content) VALUES(?, ?)",
        ("assistant", reply),
    )
    conn.commit()
    return jsonify({"reply": reply, "sources": sources})


def generate_assistant_reply(message: str):
    keywords = {token.lower().strip(".,!?") for token in message.split() if len(token) > 3}
    conn = get_connection()
    sources: List[Dict] = []
    summaries: List[str] = []

    # Bills insight
    bill_rows = conn.execute("SELECT * FROM bills").fetchall()
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
        if due_dt and due_dt - datetime.utcnow() <= timedelta(days=7):
            due_soon.append(bill)
        if keywords and any(k in (bill["name"] or "").lower() for k in keywords):
            sources.append({
                "type": "bill",
                "title": bill["name"],
                "detail": f"Due {bill['due_date']} for ${bill['amount']:.2f}",
            })
    if due_soon:
        summaries.append(
            f"{len(due_soon)} bill(s) due soon: "
            + ", ".join(f"{bill['name']} on {bill['due_date']}" for bill in due_soon[:5])
        )

    # Tasks insight
    task_rows = conn.execute("SELECT * FROM tasks").fetchall()
    open_tasks = [task for task in task_rows if task["status"] != "done"]
    if open_tasks:
        summaries.append(f"Tracking {len(open_tasks)} active task(s).")
    for task in task_rows:
        haystack = f"{task['title']} {task['description']}".lower()
        if keywords and any(k in haystack for k in keywords):
            sources.append(
                {
                    "type": "task",
                    "title": task["title"],
                    "detail": f"Status {task['status']} due {task['due_date'] or 'n/a'}",
                }
            )

    # Notes insight
    note_rows = conn.execute("SELECT * FROM notes ORDER BY created_at DESC LIMIT 5").fetchall()
    for note in note_rows:
        haystack = f"{note['title']} {note['content']}".lower()
        if keywords and any(k in haystack for k in keywords):
            sources.append(
                {
                    "type": "note",
                    "title": note["title"],
                    "detail": note["content"][:120],
                }
            )

    # Sensor insight
    sensor_rows = conn.execute("SELECT * FROM sensors").fetchall()
    alerts = []
    for sensor in sensor_rows:
        if sensor["status"] and sensor["status"].lower() != "ok":
            alerts.append(sensor)
        haystack = f"{sensor['name']} {sensor['kind']} {sensor['location']}".lower()
        if keywords and any(k in haystack for k in keywords):
            sources.append(
                {
                    "type": "sensor",
                    "title": sensor["name"],
                    "detail": f"Last {sensor['last_value']:.2f}{sensor['unit']} ({sensor['status']})",
                }
            )
    if alerts:
        summaries.append("Sensor alerts: " + ", ".join(f"{s['name']} {s['status']}" for s in alerts))

    # RAG search
    if keywords:
        query = " OR ".join(keywords)
        rag_hits = conn.execute(
            "SELECT doc_id, title, snippet(rag_docs_fts, 1, '<b>', '</b>', '…', 20) AS excerpt FROM rag_docs_fts WHERE rag_docs_fts MATCH ? LIMIT 5",
            (query,),
        ).fetchall()
        for hit in rag_hits:
            sources.append(
                {
                    "type": "doc",
                    "title": hit["title"],
                    "detail": hit["excerpt"],
                }
            )
        if rag_hits:
            summaries.append(f"Found {len(rag_hits)} knowledge base reference(s).")

    conn.close()
    if not summaries:
        summaries.append("Standing by. No anomalies detected in bills, tasks, or sensors.")
    reply = "\n".join(summaries)
    return reply, sources


@app.route("/rag/docs", methods=["GET", "POST"])
def rag_docs():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute("SELECT id, title, content, source, created_at FROM rag_docs ORDER BY created_at DESC").fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    title = payload.get("title") or "Untitled"
    content = payload.get("content", "")
    source = payload.get("source", "user")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rag_docs(title, content, source) VALUES(?,?,?)",
        (title, content, source),
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


@app.route("/tasks", methods=["GET", "POST"])
def tasks_endpoint():
    conn = get_db()
    if request.method == "GET":
        rows = conn.execute(
            "SELECT id, title, description, status, due_date, priority, scope, tags, created_at FROM tasks ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tasks(title, description, status, due_date, priority, scope, tags) VALUES(?,?,?,?,?,?,?)",
        (
            payload.get("title"),
            payload.get("description"),
            payload.get("status", "todo"),
            payload.get("due_date"),
            payload.get("priority"),
            payload.get("scope"),
            payload.get("tags"),
        ),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/tasks/<int:task_id>", methods=["PUT", "DELETE"])
def tasks_update(task_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        conn.execute(
            "UPDATE tasks SET title=?, description=?, status=?, due_date=?, priority=?, scope=?, tags=? WHERE id=?",
            (
                payload.get("title"),
                payload.get("description"),
                payload.get("status"),
                payload.get("due_date"),
                payload.get("priority"),
                payload.get("scope"),
                payload.get("tags"),
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
            "SELECT id, title, content, created_at FROM notes ORDER BY created_at DESC"
        ).fetchall()
        return jsonify([dict(row) for row in rows])
    payload = request.get_json(force=True)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notes(title, content) VALUES(?, ?)",
        (payload.get("title"), payload.get("content")),
    )
    conn.commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/notes/<int:note_id>", methods=["PUT", "DELETE"])
def notes_update(note_id: int):
    conn = get_db()
    if request.method == "PUT":
        payload = request.get_json(force=True)
        conn.execute(
            "UPDATE notes SET title=?, content=? WHERE id=?",
            (payload.get("title"), payload.get("content"), note_id),
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
            "scheduler_running": sensor_simulation_enabled() and _scheduler_started,
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
    port = int(config.get("server", {}).get("port", 5050))
    app.run(host=host, port=port, debug=False)
