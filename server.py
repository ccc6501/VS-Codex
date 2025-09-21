"""AIVA Multi-Provider Hub with GUI and tray integration for MONKY."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

# GUI / Tray imports (lazy-loaded when needed)
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

try:  # Tray support is optional in headless environments
    from PIL import Image, ImageDraw  # type: ignore
    import pystray  # type: ignore
    _PYSTRAY_AVAILABLE = True
    _PYSTRAY_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - depends on environment
    Image = ImageDraw = None  # type: ignore
    pystray = None  # type: ignore
    _PYSTRAY_AVAILABLE = False
    _PYSTRAY_ERROR = exc

import configparser
import textwrap

# ----------------------------------------------------------------------------
# Paths / configuration
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
CSS = DIST / "css"
JS = DIST / "js"
DESIGN_FILE = ROOT / "design_language.txt"
CONFIG_FILE = ROOT / "server_config.json"
MONKY_CONFIG = ROOT / "config.json"


# Load MONKY config for host/port defaults -------------------------------------------------
def _load_monky_config() -> Dict[str, Any]:
    if MONKY_CONFIG.exists():
        try:
            return json.loads(MONKY_CONFIG.read_text())
        except Exception:
            pass
    return {
        "server": {"host": "127.0.0.1", "port": 8000},
    }


MONKY_SETTINGS = _load_monky_config()
HOST = MONKY_SETTINGS.get("server", {}).get("host", "127.0.0.1")
PORT = int(MONKY_SETTINGS.get("server", {}).get("port", 8000))
START_TS = time.time()


# ----------------------------------------------------------------------------
# Environment (API keys remain server-side)
# ----------------------------------------------------------------------------
load_dotenv()  # read once
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
QWEN_KEY = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
QWEN_BASE = os.getenv("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
GENESIS_KEY = os.getenv("GENESIS_API_KEY", "")
GENESIS_BASE = os.getenv("GENESIS_BASE_URL", "https://api.ai.us.lmco.com/v1")
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").lower() != "false"
CA_BUNDLE = os.getenv("CA_BUNDLE", "")


# ----------------------------------------------------------------------------
# HTTPX helper
# ----------------------------------------------------------------------------

def _httpx_verify():
    return CA_BUNDLE if CA_BUNDLE else VERIFY_SSL


def _httpx_client_kwargs() -> Dict[str, Any]:
    return {"timeout": httpx.Timeout(60.0), "verify": _httpx_verify()}


# ----------------------------------------------------------------------------
# Design loader and asset builder
# ----------------------------------------------------------------------------
DEFAULT_DESIGN = {
    "colors": {
        "ok": "#9EFF6E",
        "warn": "#FFC857",
        "err": "#FF6B6B",
        "background": "#0B0B10",
        "text": "#E6E6EC",
        "text_dim": "#9aa0a6",
        "primary": "#4B9FFF",
        "surface": "#13131A",
    },
    "fonts": {
        "body": "system-ui, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
        "heading": "Poppins, system-ui, sans-serif",
    },
    "spacing": {"unit": "12px", "radius": "10px"},
    "components": {"sidebar_width": "360px", "chat_width": "1200px", "button_height": "36px"},
}


def load_design() -> Dict[str, Dict[str, str]]:
    cfg = configparser.ConfigParser()
    if DESIGN_FILE.exists():
        cfg.read(DESIGN_FILE)

    def section(name: str, default: Dict[str, str]) -> Dict[str, str]:
        if name in cfg:
            values = default.copy()
            values.update({k: v for k, v in cfg[name].items()})
            return values
        return default

    return {
        "colors": section("colors", DEFAULT_DESIGN["colors"]),
        "fonts": section("fonts", DEFAULT_DESIGN["fonts"]),
        "spacing": section("spacing", DEFAULT_DESIGN["spacing"]),
        "components": section("components", DEFAULT_DESIGN["components"]),
    }


def ensure_dirs() -> None:
    for p in (DIST, CSS, JS):
        p.mkdir(parents=True, exist_ok=True)


def write_css(cfg: Dict[str, Dict[str, str]]) -> None:
    c, f, s, comp = cfg["colors"], cfg["fonts"], cfg["spacing"], cfg["components"]
    css = f"""
    :root {{ --ok:{c['ok']}; --warn:{c['warn']}; --err:{c['err']}; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin:0; padding:{s['unit']};
      background:{c['background']}; color:{c['text']};
      font-family:{f['body']};
      display:flex; gap:{s['unit']}; min-height:100vh;
    }}
    h1,h2,h3 {{ font-family:{f['heading']}; color:{c['primary']}; margin:0 0 {s['unit']} 0; }}
    .sidebar {{
      width:{comp['sidebar_width']};
      background:{c['surface']}; padding:{s['unit']};
      border-radius:{s['radius']}; overflow-y:auto;
      height:calc(100vh - {s['unit']});
    }}
    .chat {{
      flex:1; max-width:{comp['chat_width']};
      background:{c['surface']}; padding:{s['unit']};
      border-radius:{s['radius']}; display:flex; flex-direction:column;
    }}
    .row {{ display:flex; gap:{s['unit']}; align-items:center; flex-wrap:wrap; }}
    .messages {{ flex:1; overflow-y:auto; margin:{s['unit']} 0; border:1px solid #2a2a2f; border-radius:{s['radius']}; padding:{s['unit']}; }}
    .meta {{ color:{c['text_dim']}; font-size:0.8em; margin-top:2px; }}
    input, select {{
      background:#141419; color:{c['text']}; border:1px solid #2a2f3a;
      border-radius:{s['radius']}; padding:8px;
    }}
    input.prompt {{ flex:1; min-width:300px; }}
    button {{
      background:{c['primary']}; color:{c['text']};
      border:none; border-radius:{s['radius']};
      height:{comp['button_height']}; cursor:pointer; padding:0 12px; white-space:nowrap;
    }}
    button.secondary {{ background:#2a2f3a; }}
    details {{ margin-bottom:{s['unit']}; }}
    .panel {{ border:1px solid #2a2a2f; border-radius:{s['radius']}; padding:{s['unit']}; }}
    .pill {{ padding:2px 8px; border-radius:999px; font-size:12px; }}
    .ok {{ background:var(--ok); color:#000; }}
    .warn {{ background:var(--warn); color:#000; }}
    .err {{ background:var(--err); color:#000; }}
    canvas {{ width:100%; height:60px; background:#0b0b10; border-radius:{s['radius']}; }}
    """
    (CSS / "style.css").write_text(textwrap.dedent(css))


def write_js() -> None:
    js = """
    const $ = (sel, root=document) => root.querySelector(sel);
    let pingSeries = [];

    function addMessage(user, text, meta){
      const m = document.createElement('div');
      m.className = 'message';
      const safe = text.replace(/</g,'&lt;');
      m.innerHTML = `<strong>${user}:</strong> ${safe}${meta?`<div class="meta">${meta}</div>`:''}`;
      $('#messages').appendChild(m);
      m.scrollIntoView();
      return m;
    }

    async function refreshModels(){
      const provider = $('#provider').value;
      const sel = $('#model');
      sel.innerHTML = '<option>Loading…</option>';
      try {
        const resp = await fetch(`/models/${provider}`);
        const data = await resp.json();
        sel.innerHTML = '';
        const models = data.models || [];
        if(models.length){
          for(const m of models){
            const id = (m.id || m).toString();
            const name = (m.name || id);
            const opt = document.createElement('option');
            opt.value = id; opt.textContent = name;
            sel.appendChild(opt);
          }
        } else {
          sel.innerHTML = '<option value="gpt-4o">gpt-4o</option>';
        }
        if(data.warning){ console.warn(data.warning); }
      } catch(err){
        console.error(err);
        sel.innerHTML = '<option value="gpt-4o">gpt-4o</option>';
      }
    }

    async function sendMessage(){
      const msg = $('#prompt').value.trim();
      if(!msg) return;
      addMessage('You', msg);
      $('#prompt').value = '';
      const provider = $('#provider').value;
      const model = $('#model').value;
      const response = await fetch('/chat', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({provider, model, message: msg})
      });
      if(!response.ok){
        const err = await response.json().catch(()=>({}));
        addMessage('System', `❌ ${response.status} ${response.statusText}`, `${err.error||''} ${err.detail||''}`.trim());
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let acc = '';
      const div = addMessage('Bot','');
      while(true){
        const {done, value} = await reader.read();
        if(done) break;
        acc += decoder.decode(value, {stream:true});
        div.innerHTML = `<strong>Bot:</strong> ${acc.replace(/</g,'&lt;')}`;
      }
      const meta = `Provider: ${provider} • Model: ${model}`;
      const metaNode = document.createElement('div');
      metaNode.className='meta'; metaNode.textContent = meta;
      div.appendChild(metaNode);
    }

    async function ping(provider){
      const t0 = performance.now();
      const resp = await fetch(`/ping/${provider}`);
      const t1 = performance.now();
      const ms = Math.round(t1 - t0);
      const data = await resp.json().catch(()=>({}));
      const badge = resp.ok ? 'ok' : 'err';
      const reason = data.detail ? ` • ${data.detail}` : '';
      addMessage('Ping', `${provider.toUpperCase()} → <span class="pill ${badge}">${resp.status} ${resp.statusText}</span> (${ms} ms)`, reason);
      pingSeries.push(resp.ok ? ms : -1);
      if(pingSeries.length>40) pingSeries.shift();
      drawSparkline();
    }

    async function pingAll(){
      for(const p of ['openai','openrouter','qwen','ollama','genesis']){
        await ping(p);
      }
    }

    async function testStorage(){
      const path = $('#local_path').value.trim();
      const resp = await fetch('/storage/test', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({path})
      });
      const data = await resp.json().catch(()=>({}));
      if(resp.ok) addMessage('Storage', `Saved test file to: ${data.wrote}`, '✔ OK');
      else addMessage('Storage', `❌ ${resp.status} ${resp.statusText}`, data.detail||'');
    }

    async function saveConfig(){
      const body = {
        local_storage_path: $('#local_path').value.trim(),
        tailscale_router: $('#tailscale').value.trim(),
        cloud_sync: {
          provider: $('#cloud_provider').value.trim(),
          base_url: $('#cloud_base').value.trim(),
          auth: {}
        }
      };
      const resp = await fetch('/config', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      const data = await resp.json().catch(()=>({}));
      addMessage('Config', `<span class="pill ${resp.ok?'ok':'err'}">${resp.status} ${resp.statusText}</span>`, data.detail||'');
    }

    async function loadConfig(){
      const resp = await fetch('/config');
      const cfg = await resp.json();
      $('#local_path').value = cfg.local_storage_path || '';
      $('#tailscale').value = cfg.tailscale_router || '';
      $('#cloud_provider').value = (cfg.cloud_sync && cfg.cloud_sync.provider) || '';
      $('#cloud_base').value = (cfg.cloud_sync && cfg.cloud_sync.base_url) || '';
    }

    async function fetchUptime(){
      const resp = await fetch('/uptime');
      const data = await resp.json();
      const start = Date.now()/1000 - (data.seconds || 0);
      setInterval(()=>{
        const secs = Math.max(0, Math.floor(Date.now()/1000 - start));
        const h = Math.floor(secs/3600), m = Math.floor((secs%3600)/60), s = secs%60;
        $('#uptime').textContent = `${h}h ${m}m ${s}s`;
      }, 1000);
    }

    function drawSparkline(){
      const canvas = $('#pingline');
      const ctx = canvas.getContext('2d');
      const w = canvas.width = canvas.clientWidth;
      const h = canvas.height = canvas.clientHeight;
      ctx.clearRect(0,0,w,h);
      if(!pingSeries.length) return;
      const values = pingSeries.map(v=> v<0 ? 0 : v);
      const max = Math.max(...values, 100);
      const step = w/Math.max(pingSeries.length-1, 1);
      ctx.beginPath();
      pingSeries.forEach((v,i)=>{
        const x = i*step;
        const y = v<0 ? h-2 : h - (v/max)*h;
        if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      });
      ctx.lineWidth = 2; ctx.strokeStyle = '#4B9FFF';
      ctx.stroke();
    }

    (async () => {
      await loadConfig();
      await refreshModels();
      await fetchUptime();
      $('#provider').addEventListener('change', refreshModels);
      $('#prompt').addEventListener('keydown', e=>{ if(e.key==='Enter'){ e.preventDefault(); sendMessage(); }});
      drawSparkline();
    })();
    """
    (JS / "app.js").write_text(textwrap.dedent(js))


def write_html() -> None:
    html = f"""
    <!doctype html><html><head>
      <meta charset="utf-8"/>
      <title>AIVA Multi-Provider Hub</title>
      <link rel="stylesheet" href="/static/css/style.css"/>
      <style>canvas{{stroke:#4B9FFF;}}</style>
    </head><body>
      <div class="sidebar">
        <h2>Control Panel</h2>

        <details open class="panel">
          <summary>Chat Settings</summary>
          <div class="row" style="margin-top:8px;">
            <label>Provider&nbsp;
              <select id="provider">
                <option value="openai">OpenAI</option>
                <option value="openrouter">OpenRouter</option>
                <option value="qwen">Qwen / GWEN</option>
                <option value="ollama">Ollama (local)</option>
                <option value="genesis">Genesis</option>
              </select>
            </label>
            <label>Model&nbsp;
              <select id="model"><option>Loading…</option></select>
            </label>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="secondary" onclick="refreshModels()">Reload Models</button>
            <button onclick="ping(document.getElementById('provider').value)">Ping Provider</button>
            <button onclick="pingAll()">Test All</button>
          </div>
        </details>

        <details class="panel">
          <summary>Storage & Network</summary>
          <div class="row" style="flex-direction:column; align-items:flex-start;">
            <label>Local storage path</label>
            <input id="local_path" style="width:100%" placeholder="Set shared storage path"/>
            <div class="row">
              <button onclick="testStorage()">Test Local Storage</button>
            </div>
            <hr style="width:100%; border:0; border-top:1px solid #2a2a2f; margin:8px 0;">
            <label>Tailscale router/base URL</label>
            <input id="tailscale" style="width:100%" placeholder="http://100.x.y.z:8000"/>
            <label>Cloud service (future-proof)</label>
            <input id="cloud_provider" placeholder="firebase | nextcloud | webdav"/>
            <input id="cloud_base" placeholder="https://… (optional)"/>
            <div class="row">
              <button onclick="saveConfig()">Save Config</button>
              <a href="/export/config" target="_blank"><button class="secondary">Export Connection JSON</button></a>
            </div>
          </div>
        </details>

        <details class="panel">
          <summary>Status</summary>
          <div class="row">Uptime: <span id="uptime">—</span></div>
          <div style="margin-top:8px;">Ping Sparkline</div>
          <canvas id="pingline" width="600" height="60"></canvas>
        </details>
      </div>

      <div class="chat">
        <h1>Multi-Provider Chat</h1>
        <div id="messages" class="messages"></div>
        <div class="row">
          <input id="prompt" class="prompt" type="text" placeholder="Type and press Enter…">
          <button onclick="sendMessage()">Send</button>
        </div>
      </div>

      <script src="/static/js/app.js"></script>
    </body></html>
    """
    (DIST / "index.html").write_text(textwrap.dedent(html))


# ----------------------------------------------------------------------------
# Persisted server config (storage etc.)
# ----------------------------------------------------------------------------

def load_server_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    cfg = {
        "local_storage_path": str(ROOT / "local_storage"),
        "tailscale_router": "",
        "cloud_sync": {"provider": "", "base_url": "", "auth": {}},
    }
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return cfg


def save_server_config(cfg: Dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


SERVER_CFG = load_server_config()
Path(SERVER_CFG["local_storage_path"]).mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------
# FastAPI application factory (based on provided builder script)
# ----------------------------------------------------------------------------

def build_assets() -> None:
    ensure_dirs()
    design = load_design()
    write_css(design)
    write_js()
    write_html()


async def _safe_json(line: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(line)
    except Exception:
        return None


async def _yield_openai_sse(client: httpx.AsyncClient, url: str, headers: Dict[str, str], payload: Dict[str, Any]):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.status_code != 200:
            text = await r.aread()
            yield f"[HTTP {r.status_code}] {text.decode(errors='ignore')}".encode()
            return
        async for raw in r.aiter_lines():
            if not raw:
                continue
            if not raw.startswith("data: "):
                continue
            datum = raw[6:].strip()
            if datum == "[DONE]":
                break
            js = await _safe_json(datum)
            if not js:
                continue
            try:
                delta = js["choices"][0].get("delta", {})
                if delta.get("content"):
                    yield delta["content"].encode()
            except Exception:
                try:
                    content = js["choices"][0]["message"]["content"]
                    if content:
                        yield content.encode()
                except Exception:
                    continue


async def _yield_ollama_stream(client: httpx.AsyncClient, url: str, payload: Dict[str, Any]):
    async with client.stream("POST", url, json=payload) as r:
        if r.status_code != 200:
            text = await r.aread()
            yield f"[HTTP {r.status_code}] {text.decode(errors='ignore')}".encode()
            return
        async for chunk in r.aiter_lines():
            if not chunk:
                continue
            js = await _safe_json(chunk)
            if not js:
                continue
            if js.get("response"):
                yield js["response"].encode()
            if js.get("done"):
                break


async def stream_openai(model: str, msg: str):
    if not OPENAI_KEY:
        yield b"[OpenAI not configured]\n"
        return
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": msg}], "stream": True}
    async with httpx.AsyncClient(**_httpx_client_kwargs()) as client:
        async for chunk in _yield_openai_sse(client, url, headers, payload):
            yield chunk


async def stream_openrouter(model: str, msg: str):
    if not OPENROUTER_KEY:
        yield b"[OpenRouter not configured]\n"
        return
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "AIVA Hub",
    }
    payload = {"model": model, "messages": [{"role": "user", "content": msg}], "stream": True}
    async with httpx.AsyncClient(**_httpx_client_kwargs()) as client:
        async for chunk in _yield_openai_sse(client, url, headers, payload):
            yield chunk


async def stream_qwen(model: str, msg: str):
    if not QWEN_KEY:
        yield b"[Qwen not configured]\n"
        return
    url = QWEN_BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {QWEN_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": msg}], "stream": True}
    async with httpx.AsyncClient(**_httpx_client_kwargs()) as client:
        async for chunk in _yield_openai_sse(client, url, headers, payload):
            yield chunk


async def stream_ollama(model: str, msg: str):
    url = "http://localhost:11434/api/generate"
    payload = {"model": model, "prompt": msg, "stream": True}
    async with httpx.AsyncClient(**_httpx_client_kwargs()) as client:
        try:
            async for chunk in _yield_ollama_stream(client, url, payload):
                yield chunk
        except httpx.RequestError as exc:
            yield f"[Ollama connection error] {exc}".encode()


async def stream_genesis(model: str, msg: str):
    if not GENESIS_KEY:
        yield b"[Genesis not configured]\n"
        return
    url = GENESIS_BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {GENESIS_KEY}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": msg}], "stream": True}
    async with httpx.AsyncClient(**_httpx_client_kwargs()) as client:
        async for chunk in _yield_openai_sse(client, url, headers, payload):
            yield chunk


async def list_models_openai() -> Dict[str, Any]:
    if not OPENAI_KEY:
        return {"models": [], "warning": "OpenAI key missing"}
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    url = "https://api.openai.com/v1/models"
    try:
        async with httpx.AsyncClient(timeout=20, verify=_httpx_verify()) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"models": [], "error": "OpenAI", "status": resp.status_code, "detail": resp.text}
            items = resp.json().get("data", [])
            ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
            preferred = [i for i in ids if any(k in i for k in ("gpt-4", "gpt-3.5", "o", "mini", "gpt-5", "omni"))]
            data = preferred or ids
            return {"models": [{"id": m, "name": m} for m in data]}
    except Exception as exc:
        return {"models": [], "error": "OpenAI", "detail": str(exc)}


async def list_models_openrouter() -> Dict[str, Any]:
    if not OPENROUTER_KEY:
        return {"models": [], "warning": "OpenRouter key missing"}
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    url = "https://openrouter.ai/api/v1/models"
    try:
        async with httpx.AsyncClient(timeout=20, verify=_httpx_verify()) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"models": [], "error": "OpenRouter", "status": resp.status_code, "detail": resp.text}
            data = resp.json().get("data", [])
            models = [{"id": m.get("id"), "name": m.get("name", m.get("id"))} for m in data]
            return {"models": models}
    except Exception as exc:
        return {"models": [], "error": "OpenRouter", "detail": str(exc)}


async def list_models_qwen() -> Dict[str, Any]:
    defaults = [
        {"id": "qwen-plus", "name": "qwen-plus"},
        {"id": "qwen-turbo", "name": "qwen-turbo"},
        {"id": "qwen2-7b-instruct", "name": "qwen2-7b-instruct"},
    ]
    if not QWEN_KEY:
        return {"models": defaults, "warning": "Qwen key missing (defaults shown)"}
    return {"models": defaults}


async def list_models_ollama() -> Dict[str, Any]:
    url = "http://localhost:11434/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"models": [], "error": "Ollama", "status": resp.status_code, "detail": resp.text}
            data = resp.json().get("models", [])
            return {"models": [{"id": m.get("name"), "name": m.get("name")} for m in data]}
    except Exception as exc:
        return {"models": [], "warning": f"Ollama not reachable: {exc}"}


async def list_models_genesis() -> Dict[str, Any]:
    if not GENESIS_KEY:
        return {"models": [], "warning": "Genesis key missing"}
    return {"models": [
        {"id": "llama-3.3-70b-instruct", "name": "llama-3.3-70b-instruct"},
        {"id": "llama-3.1-nemotron-70b-instruct", "name": "llama-3.1-nemotron-70b-instruct"},
        {"id": "gemma-3-27b-it", "name": "gemma-3-27b-it"},
        {"id": "granite-3.3-8b-instruct", "name": "granite-3.3-8b-instruct"},
        {"id": "auto", "name": "auto (→ llama-3.3-70b-instruct)"},
    ]}


async def list_assistants_genesis() -> Dict[str, Any]:
    return {"assistants": [], "warning": "Assistants API not configured."}


async def ping_provider(name: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        if name == "openai":
            res = await list_models_openai()
        elif name == "openrouter":
            res = await list_models_openrouter()
        elif name in ("qwen", "gwen"):
            res = await list_models_qwen()
        elif name == "ollama":
            res = await list_models_ollama()
        elif name == "genesis":
            res = await list_models_genesis()
        else:
            dt = round((time.perf_counter() - t0) * 1000)
            return {"provider": name, "ok": False, "status": 400, "detail": "Unknown provider", "latency_ms": dt}

        dt = round((time.perf_counter() - t0) * 1000)
        warning = res.get("warning")
        error = res.get("error")
        ok = bool(res.get("models")) and not warning and not error
        detail = res.get("detail") or warning or ""
        if error:
            status = res.get("status", 503)
        elif warning:
            status = res.get("status", 503)
        else:
            status = res.get("status", 200)
        return {"provider": name, "ok": ok, "status": status, "detail": detail, "latency_ms": dt}
    except Exception as exc:
        dt = round((time.perf_counter() - t0) * 1000)
        return {"provider": name, "ok": False, "status": 500, "detail": str(exc), "latency_ms": dt}


# ----------------------------------------------------------------------------
# FastAPI factory
# ----------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="AIVA Multi-Provider Hub", version="1.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(DIST), html=False), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (DIST / "index.html").read_text()

    @app.get("/uptime")
    def uptime():
        return {"seconds": round(time.time() - START_TS)}

    @app.get("/export/config")
    def export_config():
        return {
            "server": f"http://{HOST}:{PORT}",
            "providers": ["openai", "openrouter", "qwen", "ollama", "genesis"],
            "endpoints": {
                "chat": "POST /chat",
                "models": "GET /models/{provider}",
                "assistants_genesis": "GET /assistants/genesis",
                "ping": "GET /ping/{provider}",
                "ping_all": "GET /ping/all",
                "config": "GET/POST /config",
                "storage_test": "POST /storage/test",
                "uptime": "GET /uptime",
            },
            "paths": {
                "local_storage": SERVER_CFG.get("local_storage_path", ""),
                "tailscale_router": SERVER_CFG.get("tailscale_router", ""),
            },
        }

    @app.get("/config")
    def get_config():
        return SERVER_CFG

    @app.post("/config")
    async def set_config(req: Request):
        body = await req.json()
        SERVER_CFG.update(
            {
                "local_storage_path": body.get("local_storage_path", SERVER_CFG["local_storage_path"]),
                "tailscale_router": body.get("tailscale_router", SERVER_CFG["tailscale_router"]),
                "cloud_sync": body.get("cloud_sync", SERVER_CFG["cloud_sync"]),
            }
        )
        try:
            Path(SERVER_CFG["local_storage_path"]).mkdir(parents=True, exist_ok=True)
            save_server_config(SERVER_CFG)
            return {"detail": "saved"}
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/models/{provider}")
    async def models(provider: str):
        if provider == "openai":
            return await list_models_openai()
        if provider == "openrouter":
            return await list_models_openrouter()
        if provider in ("qwen", "gwen"):
            return await list_models_qwen()
        if provider == "ollama":
            return await list_models_ollama()
        if provider == "genesis":
            return await list_models_genesis()
        return JSONResponse({"error": "unknown provider"}, status_code=400)

    @app.get("/assistants/genesis")
    async def assistants_genesis():
        return await list_assistants_genesis()

    @app.get("/ping/{provider}")
    async def ping(provider: str):
        res = await ping_provider(provider)
        code = 200 if res["ok"] else res.get("status", 500)
        return JSONResponse(res, status_code=code)

    @app.get("/ping/all")
    async def ping_all():
        results = []
        for name in ["openai", "openrouter", "qwen", "ollama", "genesis"]:
            results.append(await ping_provider(name))
        return {"results": results}

    @app.post("/storage/test")
    async def storage_test(req: Request):
        body = await req.json()
        path = Path(body.get("path") or SERVER_CFG["local_storage_path"])
        try:
            path.mkdir(parents=True, exist_ok=True)
            target = path / f"hub_test_{int(time.time())}.txt"
            target.write_text("ok\n")
            return {"wrote": str(target)}
        except Exception as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.post("/chat")
    async def chat(req: Request):
        data = await req.json()
        provider = (data.get("provider") or "").lower()
        model = (data.get("model") or "").strip()
        message = (data.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "empty message"}, status_code=400)

        if provider == "openai":
            stream = stream_openai(model or "gpt-4o", message)
        elif provider == "openrouter":
            stream = stream_openrouter(model or "openrouter/auto", message)
        elif provider in ("qwen", "gwen"):
            stream = stream_qwen(model or "qwen-plus", message)
        elif provider == "ollama":
            stream = stream_ollama(model or "llama3", message)
        elif provider == "genesis":
            stream = stream_genesis(model or "llama-3.3-70b-instruct", message)
        else:
            return JSONResponse({"error": "unknown provider"}, status_code=400)

        return StreamingResponse(stream, media_type="text/plain")

    return app


# ----------------------------------------------------------------------------
# Server + GUI controller
# ----------------------------------------------------------------------------


def _display_available() -> bool:
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY"))


@dataclass
class PingResult:
    provider: str
    ok: bool
    status: int
    detail: str
    latency_ms: int


class HubGUI:
    providers = ["openai", "openrouter", "qwen", "ollama", "genesis"]

    def __init__(self, controller: "HubController") -> None:
        self.controller = controller
        self.root = tk.Tk()
        self.root.title("AIVA Provider Control Panel")
        self.root.configure(bg="#090b13")
        self.root.geometry("780x520")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        style = ttk.Style()
        style.theme_use("clam")

        self.status_vars = {p: tk.StringVar(value="Pending…") for p in self.providers}

        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text=f"Server: http://{HOST}:{PORT}").pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_status).pack(side="right")

        status_frame = ttk.LabelFrame(self.root, text="Provider Status", padding=12)
        status_frame.pack(fill="x", padx=12, pady=8)

        for idx, provider in enumerate(self.providers):
            row = ttk.Frame(status_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=provider.upper(), width=12).pack(side="left")
            ttk.Label(row, textvariable=self.status_vars[provider]).pack(side="left")
            ttk.Button(row, text="Ping", command=lambda p=provider: self.ping_provider(p)).pack(side="right")

        storage_frame = ttk.LabelFrame(self.root, text="Storage & Config", padding=12)
        storage_frame.pack(fill="x", padx=12, pady=8)

        self.path_var = tk.StringVar(value=self.controller.get_config().get("local_storage_path", ""))
        path_row = ttk.Frame(storage_frame)
        path_row.pack(fill="x", pady=2)
        ttk.Label(path_row, text="Storage path", width=12).pack(side="left")
        self.path_entry = ttk.Entry(path_row, textvariable=self.path_var, width=60)
        self.path_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(path_row, text="Browse", command=self.select_path).pack(side="left", padx=4)
        ttk.Button(storage_frame, text="Save + Test", command=self.save_and_test_storage).pack(anchor="e", pady=6)

        tailscale_row = ttk.Frame(storage_frame)
        tailscale_row.pack(fill="x", pady=2)
        ttk.Label(tailscale_row, text="Tailscale", width=12).pack(side="left")
        self.tailscale_var = tk.StringVar(value=self.controller.get_config().get("tailscale_router", ""))
        ttk.Entry(tailscale_row, textvariable=self.tailscale_var).pack(side="left", fill="x", expand=True)

        self.log = tk.Text(self.root, height=12, state="disabled", bg="#10121e", fg="#cbd2ff")
        self.log.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        btn_row = ttk.Frame(self.root)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_row, text="Open Web UI", command=self.open_web_ui).pack(side="left")
        ttk.Button(btn_row, text="Restart Server", command=self.controller.restart_server).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Quit", command=self.controller.shutdown).pack(side="right")

    def mainloop(self):
        self.root.after(800, self.refresh_status)
        self.root.mainloop()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        self.root.withdraw()

    def select_path(self):
        choice = filedialog.askdirectory()
        if choice:
            self.path_var.set(choice)

    def open_web_ui(self):
        import webbrowser

        webbrowser.open(f"http://{HOST}:{PORT}/")

    def save_and_test_storage(self):
        path = self.path_var.get().strip()
        cfg = self.controller.get_config()
        cfg["local_storage_path"] = path
        cfg["tailscale_router"] = self.tailscale_var.get().strip()
        ok, payload = self.controller.save_config(cfg)
        if ok:
            self.log_message("CONFIG", f"Saved configuration. Path → {path}")
            ok2, payload2 = self.controller.test_storage(path)
            if ok2:
                self.log_message("STORAGE", f"Test file written: {payload2.get('wrote')}", kind="ok")
            else:
                self.log_message("STORAGE", f"Test failed: {payload2}", kind="error")
        else:
            self.log_message("CONFIG", f"Failed to save: {payload}", kind="error")

    def refresh_status(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def ping_provider(self, provider: str):
        threading.Thread(target=self._ping_worker, args=(provider,), daemon=True).start()

    def _refresh_worker(self):
        results = self.controller.ping_all()
        self.root.after(0, lambda: self._update_status(results))

    def _ping_worker(self, provider: str):
        result = self.controller.ping(provider)
        self.root.after(0, lambda: self._update_status([result]))

    def _update_status(self, results: List[PingResult]):
        for res in results:
            status_text = f"{res.status} • {res.latency_ms} ms"
            if res.detail:
                status_text += f" • {res.detail}"
            if res.ok:
                status_text = "OK | " + status_text
            else:
                status_text = "ERR | " + status_text
            if res.provider in self.status_vars:
                self.status_vars[res.provider].set(status_text)
            self.log_message(res.provider.upper(), status_text, kind="ok" if res.ok else "error")

    def log_message(self, source: str, message: str, *, kind: str = "info"):
        self.log.configure(state="normal")
        prefix = {
            "info": "[info]",
            "ok": "[ ok ]",
            "error": "[err]",
        }.get(kind, "[info]")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')} {prefix} {source}: {message}\n")
        self.log.configure(state="disabled")
        self.log.see("end")


class HubController:
    def __init__(self, host: str = HOST, port: int = PORT) -> None:
        self.host = host
        self.port = port
        self._server_thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._tray_icon: Optional[pystray.Icon] = None
        self._gui: Optional[HubGUI] = None
        self._quitting = False
        build_assets()

    # ---------------- Server lifecycle -----------------
    def start_server(self) -> None:
        if self._server_thread and self._server_thread.is_alive():
            return

        def runner():
            config = uvicorn.Config(
                create_app,
                host=self.host,
                port=self.port,
                log_level="info",
                factory=True,
            )
            server = uvicorn.Server(config)
            self._server = server
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(server.serve())
            loop.close()

        self._server_thread = threading.Thread(target=runner, daemon=True)
        self._server_thread.start()
        self._wait_for_ready()

    def _wait_for_ready(self, timeout: int = 20) -> None:
        deadline = time.time() + timeout
        url = f"http://{self.host}:{self.port}/uptime"
        while time.time() < deadline:
            try:
                resp = requests.get(url, timeout=2)
                if resp.ok:
                    return
            except requests.RequestException:
                time.sleep(0.5)
        raise RuntimeError("Provider hub failed to start in time")

    def stop_server(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._server_thread:
            self._server_thread.join(timeout=5)
        self._server_thread = None
        self._server = None

    def restart_server(self) -> None:
        def task():
            self.log_gui("SYSTEM", "Restarting server…")
            self.stop_server()
            try:
                self.start_server()
                self.log_gui("SYSTEM", "Server restarted", kind="ok")
            except Exception as exc:
                self.log_gui("SYSTEM", f"Restart failed: {exc}", kind="error")

        threading.Thread(target=task, daemon=True).start()

    # ---------------- Tray -----------------
    def launch_tray(self) -> None:
        if self._tray_icon or not _PYSTRAY_AVAILABLE:
            if not _PYSTRAY_AVAILABLE:
                self.log_gui(
                    "TRAY",
                    f"System tray unavailable ({_PYSTRAY_ERROR})",
                    kind="error",
                )
            return
        image = self._make_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Open Control Panel", lambda: self._invoke_gui(self.show_gui)),
            pystray.MenuItem("Restart Server", lambda: self.restart_server()),
            pystray.MenuItem("Quit", lambda: self.shutdown()),
        )
        self._tray_icon = pystray.Icon("AIVA Hub", image, "AIVA Hub", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _make_icon(self) -> Image.Image:
        if not _PYSTRAY_AVAILABLE or Image is None or ImageDraw is None:
            raise RuntimeError("Tray icon not available in this environment")
        size = (64, 64)
        image = Image.new("RGB", size, color=(9, 11, 19))
        draw = ImageDraw.Draw(image)
        draw.rectangle([10, 10, 54, 54], outline=(123, 133, 255), width=2)
        draw.text((20, 24), "A", fill=(123, 133, 255))
        return image

    # ---------------- GUI helpers -----------------
    def ensure_gui(self) -> HubGUI:
        if not self._gui:
            self._gui = HubGUI(self)
        return self._gui

    def show_gui(self) -> None:
        if self._gui:
            self._gui.show()

    def log_gui(self, source: str, message: str, *, kind: str = "info") -> None:
        if self._gui:
            self._gui.log_message(source, message, kind=kind)

    def _invoke_gui(self, func):
        if self._gui:
            self._gui.root.after(0, func)

    # ---------------- HTTP helpers for GUI -----------------
    def _base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ping_all(self) -> List[PingResult]:
        url = self._base_url() + "/ping/all"
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            results = []
            for item in data.get("results", []):
                results.append(
                    PingResult(
                        provider=item.get("provider", "unknown"),
                        ok=bool(item.get("ok")),
                        status=int(item.get("status", resp.status_code)),
                        detail=str(item.get("detail", "")),
                        latency_ms=int(item.get("latency_ms", -1)),
                    )
                )
            return results
        except Exception as exc:
            return [PingResult(provider="all", ok=False, status=500, detail=str(exc), latency_ms=-1)]

    def ping(self, provider: str) -> PingResult:
        url = self._base_url() + f"/ping/{provider}"
        try:
            resp = requests.get(url, timeout=8)
            data = resp.json()
            return PingResult(
                provider=provider,
                ok=resp.ok,
                status=int(data.get("status", resp.status_code)),
                detail=str(data.get("detail", "")),
                latency_ms=int(data.get("latency_ms", -1)),
            )
        except Exception as exc:
            return PingResult(provider=provider, ok=False, status=500, detail=str(exc), latency_ms=-1)

    def get_config(self) -> Dict[str, Any]:
        url = self._base_url() + "/config"
        try:
            resp = requests.get(url, timeout=5)
            if resp.ok:
                return resp.json()
        except Exception:
            pass
        return SERVER_CFG

    def save_config(self, cfg: Dict[str, Any]) -> (bool, Any):
        url = self._base_url() + "/config"
        try:
            resp = requests.post(url, json=cfg, timeout=5)
            if resp.ok:
                return True, resp.json()
            return False, resp.json()
        except Exception as exc:
            return False, str(exc)

    def test_storage(self, path: str) -> (bool, Any):
        url = self._base_url() + "/storage/test"
        try:
            resp = requests.post(url, json={"path": path}, timeout=6)
            if resp.ok:
                return True, resp.json()
            return False, resp.json()
        except Exception as exc:
            return False, str(exc)

    # ---------------- lifecycle -----------------
    def run(self, *, headless: bool = False, open_gui: bool = True) -> None:
        self.start_server()
        if headless or not _display_available():
            print(f"AIVA provider hub running at http://{self.host}:{self.port}")
            try:
                while not self._quitting:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.shutdown()
            return

        gui = self.ensure_gui()
        if open_gui:
            gui.show()
        else:
            gui.hide()
        self.launch_tray()
        gui.mainloop()

    def shutdown(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        if self._gui:
            self._gui.root.after(0, self._gui.root.destroy)
        self.stop_server()


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def main(headless: bool = False, open_gui: bool = True) -> None:
    controller = HubController()
    controller.run(headless=headless, open_gui=open_gui)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AIVA Provider Hub")
    parser.add_argument("--headless", action="store_true", help="Run without GUI")
    parser.add_argument("--no-gui", action="store_true", help="Start without opening control panel")
    args = parser.parse_args()

    try:
        main(headless=args.headless, open_gui=not args.no_gui)
    except RuntimeError as exc:
        print(f"Failed to start server: {exc}")
