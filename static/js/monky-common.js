export async function fetchJSON(url, options = {}) {
  const opts = { method: 'GET', ...options };
  opts.headers = opts.headers ? { ...opts.headers } : {};

  if (opts.body && !(opts.body instanceof FormData) && !opts.headers['Content-Type']) {
    opts.headers['Content-Type'] = 'application/json';
    if (typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
    }
  }

  const response = await fetch(url, opts);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (err) {
      payload = text;
    }
  }
  if (!response.ok) {
    const message = payload && typeof payload === 'object'
      ? payload.detail || payload.error || JSON.stringify(payload)
      : response.statusText;
    throw new Error(message);
  }
  return payload ?? {};
}

export function showToast(message, variant = 'info') {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast ${variant}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-6px)';
  }, 3200);
  setTimeout(() => {
    toast.remove();
    if (!container.childElementCount) {
      container.remove();
    }
  }, 3800);
}

export function formatDate(value, { withTime = false } = {}) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const options = { year: 'numeric', month: 'short', day: 'numeric' };
  if (withTime) {
    options.hour = '2-digit';
    options.minute = '2-digit';
  }
  return date.toLocaleString(undefined, options);
}

export function relativeTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  const minutes = Math.round(diff / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.round(months / 12);
  return `${years}y ago`;
}

export function createElement(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    if (key === 'class') {
      el.className = value;
    } else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.substring(2), value);
    } else if (key === 'dataset') {
      Object.entries(value).forEach(([dataKey, dataValue]) => {
        el.dataset[dataKey] = dataValue;
      });
    } else {
      el.setAttribute(key, value);
    }
  });
  children.flat().forEach(child => {
    if (child === null || child === undefined) return;
    if (child instanceof Node) {
      el.appendChild(child);
    } else {
      el.appendChild(document.createTextNode(child));
    }
  });
  return el;
}

export async function copyToClipboard(text) {
  if (!navigator.clipboard) {
    showToast('Clipboard API not available in this browser.', 'danger');
    return false;
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function renderSources(container, sources = []) {
  container.innerHTML = '';
  if (!sources || !sources.length) return;
  sources.forEach(src => {
    const pill = createElement('span', { class: 'pill secondary' }, `${src.kind || 'source'} ▸ ${src.title || ''}`);
    if (src.detail) {
      pill.title = src.detail;
    }
    container.appendChild(pill);
  });
}

export const api = {
  env: () => fetchJSON('/env'),
  dashboard: scope => fetchJSON(`/dashboard/${scope}`),
  assistant: {
    providers: () => fetchJSON('/assistant/providers'),
    threads: () => fetchJSON('/assistant/threads'),
    createThread: data => fetchJSON('/assistant/threads', { method: 'POST', body: data }),
    renameThread: (id, data) => fetchJSON(`/assistant/threads/${id}`, { method: 'PUT', body: data }),
    deleteThread: id => fetchJSON(`/assistant/threads/${id}`, { method: 'DELETE' }),
    messages: threadId => {
      const query = threadId ? `?thread_id=${threadId}` : '';
      return fetchJSON(`/assistant/messages${query}`);
    },
    send: data => fetchJSON('/assistant/send', { method: 'POST', body: data }),
    ping: provider => fetchJSON(`/assistant/ping/${provider}`),
    pingAll: () => fetchJSON('/assistant/ping'),
    test: data => fetchJSON('/assistant/test', { method: 'POST', body: data }),
  },
  projects: {
    list: () => fetchJSON('/projects'),
    create: data => fetchJSON('/projects', { method: 'POST', body: data }),
    update: (id, data) => fetchJSON(`/projects/${id}`, { method: 'PUT', body: data }),
    remove: id => fetchJSON(`/projects/${id}`, { method: 'DELETE' }),
  },
  tasks: {
    list: () => fetchJSON('/tasks'),
    create: data => fetchJSON('/tasks', { method: 'POST', body: data }),
    update: (id, data) => fetchJSON(`/tasks/${id}`, { method: 'PUT', body: data }),
    remove: id => fetchJSON(`/tasks/${id}`, { method: 'DELETE' }),
  },
  notes: {
    list: () => fetchJSON('/notes'),
    create: data => fetchJSON('/notes', { method: 'POST', body: data }),
    update: (id, data) => fetchJSON(`/notes/${id}`, { method: 'PUT', body: data }),
    remove: id => fetchJSON(`/notes/${id}`, { method: 'DELETE' }),
  },
  kpi: {
    list: () => fetchJSON('/kpi/datasets'),
    upload: formData => fetchJSON('/kpi/upload', { method: 'POST', body: formData }),
    remove: id => fetchJSON(`/kpi/datasets/${id}`, { method: 'DELETE' }),
  },
  dataAssets: {
    list: () => fetchJSON('/data/assets'),
    create: data => fetchJSON('/data/assets', { method: 'POST', body: data }),
    update: (id, data) => fetchJSON(`/data/assets/${id}`, { method: 'PUT', body: data }),
    remove: id => fetchJSON(`/data/assets/${id}`, { method: 'DELETE' }),
  },
  rag: {
    list: () => fetchJSON('/rag/docs'),
    create: data => fetchJSON('/rag/docs', { method: 'POST', body: data }),
    remove: id => fetchJSON(`/rag/docs/${id}`, { method: 'DELETE' }),
    search: query => fetchJSON('/rag/query', { method: 'POST', body: { query } }),
  },
  bills: {
    list: () => fetchJSON('/bills'),
    create: data => fetchJSON('/bills', { method: 'POST', body: data }),
    update: (id, data) => fetchJSON(`/bills/${id}`, { method: 'PUT', body: data }),
    remove: id => fetchJSON(`/bills/${id}`, { method: 'DELETE' }),
    ledger: (id, data) => fetchJSON(`/bills/${id}/ledger`, { method: 'POST', body: data }),
  },
  budgetSummary: () => fetchJSON('/budget/summary'),
  sensors: {
    list: () => fetchJSON('/sensors'),
    readings: id => fetchJSON(`/sensors/${id}/readings`),
  },
  vault: {
    auth: pin => fetchJSON('/vault/auth', { method: 'POST', body: { pin } }),
    list: pin => fetchJSON('/vault/items/list', { method: 'POST', body: { pin } }),
    create: data => fetchJSON('/vault/items', { method: 'POST', body: data }),
    update: (id, data) => fetchJSON(`/vault/items/${id}`, { method: 'PUT', body: data }),
    remove: (id, pin) => fetchJSON(`/vault/items/${id}`, { method: 'DELETE', body: { pin } }),
  },
  dev: {
    status: () => fetchJSON('/dev/status'),
    backup: () => fetchJSON('/dev/backup', { method: 'POST' }),
    restore: name => fetchJSON('/dev/restore', { method: 'POST', body: { backup: name } }),
    toggles: data => fetchJSON('/dev/toggles', { method: 'POST', body: data }),
    ping: url => fetchJSON('/dev/ping', { method: 'POST', body: { url } }),
  },
  settings: {
    config: () => fetchJSON('/settings/config'),
    update: updates => fetchJSON('/settings/config', { method: 'POST', body: updates }),
  },
  storage: {
    test: body => fetchJSON('/storage/test', { method: 'POST', body }),
  },
  budget: {
    import: formData => fetchJSON('/budget/import', { method: 'POST', body: formData }),
  },
};

export function bindCopyButtons(root) {
  root.querySelectorAll('[data-copy]').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
      const text = btn.getAttribute('data-copy') || btn.textContent || '';
      const ok = await copyToClipboard(text.trim());
      showToast(ok ? 'Copied to clipboard' : 'Copy failed', ok ? 'success' : 'danger');
    });
  });
}
