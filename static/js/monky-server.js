import { api, showToast, formatDate } from './monky-common.js';

const state = {
  status: null,
  config: null,
  env: null,
  providers: [],
};

document.addEventListener('DOMContentLoaded', () => {
  setupClock();
  setupEvents();
  loadAll();
});

function setupClock() {
  const clockEl = document.getElementById('server-clock');
  const update = () => {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  update();
  setInterval(update, 60000);
}

async function loadAll() {
  await Promise.all([loadStatus(), loadConfig(), loadEnv(), loadProviders()]);
}

async function loadStatus() {
  try {
    state.status = await api.dev.status();
    renderStatus();
  } catch (err) {
    showToast(`Status load failed: ${err.message}`, 'danger');
  }
}

async function loadConfig() {
  try {
    state.config = await api.settings.config();
    renderConfig();
    populateCredentialForm();
    populateStorageFields();
  } catch (err) {
    showToast(`Config load failed: ${err.message}`, 'danger');
  }
}

async function loadEnv() {
  try {
    state.env = await api.env();
    renderMeta();
  } catch (err) {
    showToast(`Env load failed: ${err.message}`, 'danger');
  }
}

async function loadProviders() {
  try {
    const payload = await api.assistant.providers();
    state.providers = payload.providers || [];
    renderProviders();
    populateProviderSelect();
  } catch (err) {
    showToast(`Providers load failed: ${err.message}`, 'danger');
  }
}

function renderMeta() {
  const meta = document.getElementById('server-meta');
  meta.innerHTML = '';
  if (!state.env) return;
  const badges = [];
  if (state.env.features) {
    const enabled = Object.entries(state.env.features)
      .filter(([, flag]) => flag)
      .map(([name]) => name)
      .join(', ');
    badges.push(`Apps: ${enabled || 'none'}`);
  }
  badges.push(`Embeddings ${state.env.assistant_embeddings ? 'on' : 'off'}`);
  badges.forEach(text => {
    const span = document.createElement('span');
    span.textContent = text;
    meta.appendChild(span);
  });
}

function renderStatus() {
  if (!state.status) return;
  const body = document.getElementById('server-status-body');
  body.innerHTML = '';
  const entries = [
    `Sensors · ${state.status.sensor_count}`,
    `Open tasks · ${state.status.open_tasks}`,
    `Bills tracked · ${state.status.bill_count}`,
    `Latest backup · ${state.status.latest_backup || 'none'}`,
    `Database · ${state.status.db_path}`,
    `Scheduler · ${state.status.scheduler_running ? 'running' : 'paused'}`,
  ];
  entries.forEach(item => {
    const div = document.createElement('div');
    div.textContent = item;
    body.appendChild(div);
  });

  if (state.status.storage) {
    document.getElementById('storage-work').value = state.status.storage.work || '';
    document.getElementById('storage-home').value = state.status.storage.home || '';
    document.getElementById('storage-shared').value = state.status.storage.shared || '';
  }

  document.getElementById('server-host').textContent = state.config?.server
    ? `${state.config.server.host}:${state.config.server.port}`
    : 'local';
}

function renderConfig() {
  const textarea = document.getElementById('server-config');
  textarea.value = JSON.stringify(state.config, null, 2);
}

function populateCredentialForm() {
  const form = document.getElementById('server-credentials-form');
  if (!state.config?.integrations) return;
  form.querySelectorAll('input').forEach(input => {
    const path = input.name.split('.');
    let value = state.config;
    path.forEach(segment => {
      value = value ? value[segment] : undefined;
    });
    if (typeof value === 'string') {
      input.value = value;
    }
  });
}

function populateStorageFields() {
  const work = document.getElementById('storage-work');
  const home = document.getElementById('storage-home');
  const shared = document.getElementById('storage-shared');
  if (!state.config?.storage) return;
  work.value = state.config.storage.work?.root || work.value;
  home.value = state.config.storage.home?.root || home.value;
  shared.value = state.config.storage.shared?.mount_path || shared.value;
}

function renderProviders() {
  const container = document.getElementById('server-ping-list');
  container.innerHTML = '';
  state.providers.forEach(provider => {
    const row = document.createElement('li');
    row.className = 'list-item';
    const info = document.createElement('div');
    info.className = 'info';
    const label = document.createElement('h4');
    label.textContent = provider.label || provider.id;
    info.appendChild(label);
    const detail = document.createElement('p');
    detail.textContent = provider.has_key ? `Model · ${provider.model || ''}` : 'Credential missing';
    info.appendChild(detail);
    row.appendChild(info);

    const meta = document.createElement('div');
    meta.className = 'meta';
    const button = document.createElement('button');
    button.className = 'button secondary';
    button.textContent = 'Ping';
    button.addEventListener('click', () => pingProvider(provider.id, row));
    meta.appendChild(button);
    const badge = document.createElement('span');
    badge.className = provider.has_key ? 'pill success' : 'pill warn';
    badge.textContent = provider.has_key ? 'Key set' : 'Missing key';
    meta.appendChild(badge);
    row.appendChild(meta);
    container.appendChild(row);
  });
}

function populateProviderSelect() {
  const select = document.getElementById('server-request-provider');
  select.innerHTML = '';
  state.providers.forEach(provider => {
    const option = document.createElement('option');
    option.value = provider.id;
    option.textContent = provider.label || provider.id;
    select.appendChild(option);
  });
}

function setupEvents() {
  document.getElementById('server-refresh').addEventListener('click', loadStatus);
  document.getElementById('server-reload-config').addEventListener('click', loadConfig);
  document.getElementById('server-save-config').addEventListener('click', saveConfig);
  document.getElementById('server-save-credentials').addEventListener('click', saveCredentials);
  document.getElementById('server-refresh-providers').addEventListener('click', loadProviders);
  document.getElementById('server-ping-all').addEventListener('click', pingAllProviders);
  document.getElementById('server-backup').addEventListener('click', backupDatabase);
  document.getElementById('server-restore').addEventListener('submit', restoreDatabase);
  document.getElementById('server-open-launcher').addEventListener('click', () => window.open('/launch', '_blank'));
  document.getElementById('server-open-work').addEventListener('click', () => window.open('/work', '_blank'));
  document.getElementById('server-open-home').addEventListener('click', () => window.open('/home', '_blank'));
  document.getElementById('server-request-send').addEventListener('click', sendTestRequest);
  document.getElementById('server-request-clear').addEventListener('click', () => {
    document.getElementById('server-request-body').value = '';
    document.getElementById('server-request-result').textContent = '';
  });

  document.querySelectorAll('#server-storage button[data-scope]').forEach(button => {
    button.addEventListener('click', () => testStorage(button.dataset.scope));
  });
}

function collectFormValues(form) {
  const data = {};
  form.querySelectorAll('input').forEach(input => {
    const path = input.name.split('.');
    let cursor = data;
    path.forEach((segment, index) => {
      if (index === path.length - 1) {
        cursor[segment] = input.value;
      } else {
        cursor[segment] = cursor[segment] || {};
        cursor = cursor[segment];
      }
    });
  });
  return data;
}

async function saveCredentials() {
  try {
    const form = document.getElementById('server-credentials-form');
    const updates = collectFormValues(form);
    await api.settings.update(updates);
    showToast('Provider settings saved', 'success');
    await Promise.all([loadConfig(), loadProviders()]);
  } catch (err) {
    showToast(`Save failed: ${err.message}`, 'danger');
  }
}

async function saveConfig() {
  try {
    const textarea = document.getElementById('server-config');
    const parsed = JSON.parse(textarea.value);
    await api.settings.update(parsed);
    showToast('Configuration saved', 'success');
    await loadConfig();
  } catch (err) {
    showToast(`Config save failed: ${err.message}`, 'danger');
  }
}

async function pingProvider(providerId, row) {
  try {
    const result = await api.assistant.ping(providerId);
    renderPingResult(row, result);
  } catch (err) {
    showToast(`Ping failed: ${err.message}`, 'danger');
  }
}

async function pingAllProviders() {
  try {
    const payload = await api.assistant.pingAll();
    payload.results.forEach(result => {
      const row = Array.from(document.querySelectorAll('#server-ping-list .list-item'))
        .find(item => item.querySelector('h4')?.textContent.includes(result.provider) || item.querySelector('h4')?.textContent.toLowerCase().includes(result.provider));
      if (row) renderPingResult(row, result);
    });
    showToast('Ping sweep complete', 'success');
  } catch (err) {
    showToast(`Ping sweep failed: ${err.message}`, 'danger');
  }
}

function renderPingResult(row, result) {
  const meta = row.querySelector('.meta');
  let badge = meta.querySelector('.ping-result');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'pill ping-result';
    meta.appendChild(badge);
  }
  badge.className = `pill ping-result ${result.ok ? 'success' : 'danger'}`;
  badge.textContent = result.ok ? `OK · ${result.latency_ms}ms` : `Error`;
  const info = row.querySelector('.info');
  let detail = info.querySelector('small.ping-detail');
  if (!detail) {
    detail = document.createElement('small');
    detail.className = 'ping-detail';
    info.appendChild(detail);
  }
  detail.textContent = result.detail || '';
}

async function testStorage(scope) {
  try {
    const pathInput = {
      work: document.getElementById('storage-work'),
      home: document.getElementById('storage-home'),
      shared: document.getElementById('storage-shared'),
    }[scope];
    const body = { scope, path: pathInput?.value }; 
    const res = await api.storage.test(body);
    document.getElementById('storage-result').textContent = `OK → ${res.test_file}`;
    showToast('Storage path verified', 'success');
  } catch (err) {
    document.getElementById('storage-result').textContent = err.message;
    showToast(`Storage test failed: ${err.message}`, 'danger');
  }
}

async function sendTestRequest() {
  const provider = document.getElementById('server-request-provider').value;
  const model = document.getElementById('server-request-model').value.trim();
  const message = document.getElementById('server-request-body').value.trim();
  if (!message) {
    showToast('Enter a message to test', 'warn');
    return;
  }
  const resultBox = document.getElementById('server-request-result');
  resultBox.textContent = 'Sending…';
  try {
    const payload = await api.assistant.test({ provider, model, message });
    const reply = payload.reply || payload.error || JSON.stringify(payload, null, 2);
    resultBox.textContent = reply;
    showToast('Provider responded', payload.error ? 'warn' : 'success');
  } catch (err) {
    resultBox.textContent = err.message;
    showToast(`Test failed: ${err.message}`, 'danger');
  }
}

async function backupDatabase() {
  try {
    const res = await api.dev.backup();
    showToast(`Backup created: ${res.backup}`, 'success');
  } catch (err) {
    showToast(`Backup failed: ${err.message}`, 'danger');
  }
}

async function restoreDatabase(event) {
  event.preventDefault();
  const formData = new FormData(event.target);
  const name = formData.get('name');
  try {
    await api.dev.restore(name);
    showToast('Restore triggered', 'success');
  } catch (err) {
    showToast(`Restore failed: ${err.message}`, 'danger');
  }
}
