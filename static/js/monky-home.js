import { api, showToast, formatDate, relativeTime, createElement, renderSources, bindCopyButtons } from './monky-common.js';

const state = {
  env: {},
  tabsLoaded: { home: false, assistant: false, projects: false, budget: false, sensors: false, vault: false, dev: false },
  assistant: { providers: [], provider: 'local', threads: [], threadId: null, messages: [] },
  projects: [],
  tasks: [],
  notes: [],
  dashboard: null,
  budget: null,
  sensors: [],
  vault: { pin: '', unlocked: false, items: [] },
};

const elements = {
  nav: document.getElementById('home-nav'),
  topMeta: document.getElementById('home-top-meta'),
  providerPill: document.getElementById('home-provider-pill'),
  clock: document.getElementById('home-clock'),
  tabs: Array.from(document.querySelectorAll('.monky-tab')),
};

let clockInterval;

document.addEventListener('DOMContentLoaded', () => {
  setupNav();
  setupClock();
  loadEnv();
  activateTab('home');
  setupProjectsTab();
  setupBudgetTab();
  setupVaultTab();
  setupDevTab();
  setupAssistantModal();
});

function setupNav() {
  elements.nav.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.classList.contains('active')) return;
      elements.nav.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activateTab(btn.dataset.tab);
    });
  });
}

function setupClock() {
  const update = () => {
    const now = new Date();
    elements.clock.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  update();
  clockInterval = setInterval(update, 60000);
}

async function loadEnv() {
  try {
    state.env = await api.env();
  } catch (err) {
    showToast(`Env load failed: ${err.message}`, 'danger');
  }
}

function activateTab(tabId) {
  elements.tabs.forEach(tab => tab.classList.toggle('active', tab.id === `home-tab-${tabId}`));
  if (state.tabsLoaded[tabId]) return;
  switch (tabId) {
    case 'home':
      loadHome();
      break;
    case 'assistant':
      initAssistant();
      break;
    case 'projects':
      refreshProjectsSection();
      break;
    case 'budget':
      refreshBudget();
      break;
    case 'sensors':
      refreshSensors();
      break;
    case 'dev':
      refreshDev();
      break;
    default:
      break;
  }
  state.tabsLoaded[tabId] = true;
}

async function loadHome() {
  try {
    const summary = await api.dashboard('home');
    state.dashboard = summary;
    renderHome(summary);
  } catch (err) {
    showToast(`Home load failed: ${err.message}`, 'danger');
  }
}

function renderHome(summary) {
  const cards = document.getElementById('home-overview-cards');
  cards.innerHTML = '';
  const stats = [
    { label: 'Active Projects', value: summary.projects ? summary.projects.filter(p => (p.status || 'active') !== 'done').length : 0 },
    { label: 'Open Tasks', value: summary.tasks ? summary.tasks.filter(t => (t.status || 'todo') !== 'done').length : 0 },
    { label: 'Sensors Online', value: summary.sensors ? summary.sensors.length : 0 },
    { label: 'Bills Tracked', value: summary.bills ? summary.bills.length : 0 },
  ];
  stats.forEach(stat => {
    cards.appendChild(createElement('div', { class: 'stat-card' },
      createElement('span', { class: 'muted' }, stat.label),
      createElement('strong', {}, String(stat.value))
    ));
  });

  const focusList = document.getElementById('home-focus-list');
  focusList.innerHTML = '';
  const focusEntries = [];
  if (summary.tasks) {
    focusEntries.push(...summary.tasks.filter(t => (t.status || 'todo') !== 'done').slice(0, 3).map(t => ({
      title: t.title,
      detail: t.description,
      badge: t.status,
      meta: t.due_date ? `Due ${formatDate(t.due_date)}` : 'No due date',
    })));
  }
  if (summary.bills) {
    focusEntries.push(...summary.bills.slice(0, 2).map(b => ({
      title: b.name,
      detail: `$${Number(b.amount || 0).toFixed(2)}`,
      badge: b.status,
      meta: b.due_date ? `Due ${formatDate(b.due_date)}` : '',
    })));
  }
  focusEntries.slice(0, 5).forEach(entry => {
    focusList.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, entry.title || 'Item'),
        createElement('p', {}, entry.detail || '—')
      ),
      createElement('div', { class: 'meta' },
        createElement('span', { class: 'pill secondary' }, entry.badge || ''),
        createElement('small', {}, entry.meta || '')
      )
    ));
  });

  const billsList = document.getElementById('home-bills-list');
  billsList.innerHTML = '';
  (summary.bills || []).slice(0, 5).forEach(bill => {
    billsList.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, bill.name || 'Bill'),
        createElement('p', {}, `$${Number(bill.amount || 0).toFixed(2)} · ${bill.category || 'uncategorized'}`)
      ),
      createElement('div', { class: 'meta' },
        createElement('span', { class: 'pill secondary' }, bill.status || 'scheduled'),
        createElement('small', {}, bill.due_date ? formatDate(bill.due_date) : 'No due')
      )
    ));
  });

  const sensorsList = document.getElementById('home-sensors-list');
  sensorsList.innerHTML = '';
  (summary.sensors || []).slice(0, 5).forEach(sensor => {
    sensorsList.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, sensor.name || 'Sensor'),
        createElement('p', {}, `${sensor.last_value ?? '—'} ${sensor.unit || ''}`)
      ),
      createElement('div', { class: 'meta' },
        createElement('span', { class: sensor.status && sensor.status.toLowerCase() === 'ok' ? 'pill success' : 'pill warn' }, sensor.status || 'status'),
        createElement('small', {}, relativeTime(sensor.last_updated))
      )
    ));
  });

  updateTopMeta(summary);
}

function updateTopMeta(summary) {
  elements.topMeta.innerHTML = '';
  const alerts = [];
  if (summary.bills) {
    const dueSoon = summary.bills.filter(b => b.due_date).slice(0, 1);
    if (dueSoon.length) alerts.push(`Next bill ${dueSoon[0].name} · ${formatDate(dueSoon[0].due_date)}`);
  }
  if (summary.sensors) {
    const alerting = summary.sensors.filter(s => (s.status || '').toLowerCase() !== 'ok');
    if (alerting.length) alerts.push(`${alerting.length} sensor alert${alerting.length > 1 ? 's' : ''}`);
  }
  if (summary.tasks) {
    const open = summary.tasks.filter(t => (t.status || 'todo') !== 'done').length;
    alerts.push(`${open} home tasks active`);
  }
  alerts.forEach(text => elements.topMeta.appendChild(createElement('span', {}, text)));
}

async function initAssistant() {
  if (state.tabsLoaded.assistant_init) return;
  try {
    const providerData = await api.assistant.providers();
    state.assistant.providers = providerData.providers || [];
    state.assistant.provider = providerData.default || (state.assistant.providers[0]?.id || 'local');
    populateProviderSelects();
    await ensureThread();
    await refreshThreads(state.assistant.threadId);
    setupAssistantEvents();
    state.tabsLoaded.assistant_init = true;
    state.tabsLoaded.assistant = true;
  } catch (err) {
    showToast(`Assistant init failed: ${err.message}`, 'danger');
  }
}

async function ensureThread() {
  const threads = await api.assistant.threads();
  if (!threads.length) {
    const created = await api.assistant.createThread({ title: 'Home Thread' });
    state.assistant.threadId = created.id;
  } else {
    state.assistant.threadId = threads[0].id;
  }
}

function populateProviderSelects() {
  const selects = [
    document.getElementById('home-assistant-provider'),
    document.getElementById('home-modal-provider'),
  ];
  selects.forEach(select => {
    if (!select) return;
    select.innerHTML = '';
    state.assistant.providers.forEach(provider => {
      const option = createElement('option', { value: provider.id }, `${provider.label}${provider.has_key ? '' : ' · simulated'}`);
      option.dataset.model = provider.model || '';
      if (provider.id === state.assistant.provider) option.selected = true;
      select.appendChild(option);
    });
  });
  elements.providerPill.textContent = `Provider · ${state.assistant.provider}`;
  syncModalProvider();
}

function syncModalProvider() {
  const modalSelect = document.getElementById('home-modal-provider');
  const modelInput = document.getElementById('home-modal-model');
  if (!modalSelect || !modelInput) return;
  modalSelect.value = state.assistant.provider;
  const selected = modalSelect.selectedOptions[0];
  modelInput.placeholder = selected && selected.dataset.model
    ? `Model ${selected.dataset.model}`
    : 'Model override (optional)';
}

async function refreshThreads(activeId) {
  const threads = await api.assistant.threads();
  state.assistant.threads = threads;
  if (!threads.length) {
    await ensureThread();
    return refreshThreads(state.assistant.threadId);
  }
  activeId = activeId || state.assistant.threadId || threads[0].id;
  renderThreads(activeId);
  await loadMessages(activeId);
}

function renderThreads(activeId) {
  const list = document.getElementById('home-thread-list');
  list.innerHTML = '';
  state.assistant.threads.forEach(thread => {
    const btn = createElement('button', { class: thread.id === activeId ? 'active' : '' },
      createElement('div', {}, thread.title || `Thread ${thread.id}`),
      createElement('small', {}, thread.preview || 'No messages yet')
    );
    btn.addEventListener('click', () => loadMessages(thread.id));
    list.appendChild(btn);
  });
  state.assistant.threadId = activeId;
}

async function loadMessages(threadId) {
  try {
    const payload = await api.assistant.messages(threadId);
    state.assistant.threadId = payload.thread.id;
    state.assistant.messages = payload.messages || [];
    renderMessages();
    renderModalMessages();
  } catch (err) {
    showToast(`Chat load failed: ${err.message}`, 'danger');
  }
}

function renderMessages() {
  const container = document.getElementById('home-assistant-messages');
  container.innerHTML = '';
  state.assistant.messages.forEach(message => {
    const card = createElement('div', { class: `chat-message ${message.role}` });
    const header = createElement('header', {},
      createElement('span', {}, message.role === 'assistant' ? 'MONKY' : 'You'),
      createElement('small', {}, formatDate(message.created_at, { withTime: true }))
    );
    const controls = createElement('div', { class: 'chat-actions' });
    const copyBtn = createElement('button', { class: 'button secondary', 'data-copy': message.content }, 'Copy');
    controls.appendChild(copyBtn);
    header.appendChild(controls);
    const body = createElement('div');
    body.innerHTML = formatMarkdown(message.content || '');
    const sources = createElement('div', { class: 'chat-sources' });
    renderSources(sources, message.sources);

    card.appendChild(header);
    card.appendChild(body);
    if (message.sources && message.sources.length) card.appendChild(sources);
    container.appendChild(card);
  });
  container.scrollTop = container.scrollHeight;
  bindCopyButtons(container);
}

function renderModalMessages() {
  const container = document.getElementById('home-modal-messages');
  if (!container) return;
  container.innerHTML = '';
  state.assistant.messages.slice(-8).forEach(message => {
    container.appendChild(createElement('div', { class: `chat-message ${message.role}` },
      createElement('header', {},
        createElement('span', {}, message.role === 'assistant' ? 'MONKY' : 'You'),
        createElement('small', {}, relativeTime(message.created_at))
      ),
      createElement('div', {}, message.content)
    ));
  });
  container.scrollTop = container.scrollHeight;
}

function setupAssistantEvents() {
  document.getElementById('home-assistant-provider').addEventListener('change', e => {
    state.assistant.provider = e.target.value;
    syncModalProvider();
    elements.providerPill.textContent = `Provider · ${state.assistant.provider}`;
  });
  document.getElementById('home-modal-provider').addEventListener('change', e => {
    state.assistant.provider = e.target.value;
    document.getElementById('home-assistant-provider').value = state.assistant.provider;
    syncModalProvider();
    elements.providerPill.textContent = `Provider · ${state.assistant.provider}`;
  });
  document.getElementById('home-assistant-send').addEventListener('click', () => sendAssistantMessage('main'));
  document.getElementById('home-assistant-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendAssistantMessage('main');
  });
  document.getElementById('home-assistant-refresh').addEventListener('click', () => loadMessages(state.assistant.threadId));
  document.getElementById('home-thread-new').addEventListener('click', async () => {
    try {
      const created = await api.assistant.createThread({ title: `Thread ${state.assistant.threads.length + 1}` });
      showToast('Thread created', 'success');
      await refreshThreads(created.id);
    } catch (err) {
      showToast(`Thread create failed: ${err.message}`, 'danger');
    }
  });
}

async function sendAssistantMessage(source) {
  const textarea = source === 'main' ? document.getElementById('home-assistant-input') : document.getElementById('home-modal-input');
  const providerSelect = source === 'main' ? document.getElementById('home-assistant-provider') : document.getElementById('home-modal-provider');
  const modelInput = source === 'main' ? null : document.getElementById('home-modal-model');
  const message = textarea.value.trim();
  if (!message) {
    showToast('Message cannot be empty', 'warn');
    return;
  }
  try {
    await api.assistant.send({
      thread_id: state.assistant.threadId,
      provider: providerSelect.value,
      model: modelInput ? modelInput.value.trim() || modelInput.dataset.model : undefined,
      message,
    });
    textarea.value = '';
    showToast('MONKY responding…', 'success');
    await loadMessages(state.assistant.threadId);
  } catch (err) {
    showToast(`Assistant error: ${err.message}`, 'danger');
  }
}

function formatMarkdown(text) {
  const escaped = (text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const blocks = escaped.split(/\n\n+/).map(block => block.startsWith('```')
    ? `<pre><code>${block.replace(/```/g, '').trim()}</code></pre>`
    : `<p>${block.replace(/\n/g, '<br />')}</p>`);
  return blocks.join('');
}

function setupAssistantModal() {
  const modal = document.getElementById('home-assistant-modal');
  const openBtn = document.getElementById('home-assistant-open-modal');
  const closeBtn = document.getElementById('home-assistant-modal-close');
  const sendBtn = document.getElementById('home-modal-send');
  const input = document.getElementById('home-modal-input');

  if (openBtn) openBtn.addEventListener('click', () => {
    modal.classList.remove('hidden');
    renderModalMessages();
    syncModalProvider();
    input.focus();
  });
  if (closeBtn) closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
  if (sendBtn) sendBtn.addEventListener('click', () => sendAssistantMessage('modal'));
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') modal.classList.add('hidden');
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) sendAssistantMessage('modal');
    });
  }
  modal.addEventListener('click', e => {
    if (e.target === modal) modal.classList.add('hidden');
  });
}

function setupProjectsTab() {
  const subtabs = document.getElementById('home-project-tabs');
  subtabs.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      subtabs.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'));
      ['projects', 'tasks', 'notes'].forEach(section => {
        document.getElementById(`home-${section}-section`).hidden = section !== btn.dataset.target;
      });
    });
  });

  document.getElementById('home-project-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.projects.create(data);
      e.target.reset();
      await refreshProjectsSection();
      showToast('Project saved', 'success');
    } catch (err) {
      showToast(`Project error: ${err.message}`, 'danger');
    }
  });

  document.getElementById('home-task-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.tasks.create(data);
      e.target.reset();
      await refreshProjectsSection();
      showToast('Task saved', 'success');
    } catch (err) {
      showToast(`Task error: ${err.message}`, 'danger');
    }
  });

  document.getElementById('home-note-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.notes.create(data);
      e.target.reset();
      await refreshProjectsSection();
      showToast('Note saved', 'success');
    } catch (err) {
      showToast(`Note error: ${err.message}`, 'danger');
    }
  });
}

async function refreshProjectsSection() {
  try {
    const [projects, tasks, notes] = await Promise.all([api.projects.list(), api.tasks.list(), api.notes.list()]);
    state.projects = projects;
    state.tasks = tasks;
    state.notes = notes;
    renderProjects();
    renderTasks();
    renderNotes();
    populateProjectSelects();
  } catch (err) {
    showToast(`Project load failed: ${err.message}`, 'danger');
  }
}

function populateProjectSelects() {
  const selects = [document.getElementById('home-task-project'), document.getElementById('home-note-project')];
  selects.forEach(select => {
    select.innerHTML = '<option value="">No project</option>';
    state.projects.forEach(project => select.appendChild(createElement('option', { value: project.id }, project.name || `Project ${project.id}`)));
  });
}

function renderProjects() {
  const list = document.getElementById('home-projects-list');
  list.innerHTML = '';
  state.projects.forEach(project => {
    list.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, project.name || 'Project'),
        createElement('p', {}, project.description || '—')
      ),
      createElement('div', { class: 'meta' },
        createElement('select', {
          onchange: e => updateProject(project.id, { status: e.target.value }),
        }, ...['active', 'planning', 'blocked', 'done'].map(status => {
          const option = createElement('option', { value: status }, status);
          if (status === (project.status || 'active')) option.selected = true;
          return option;
        })),
        createElement('small', {}, project.due_date ? formatDate(project.due_date) : 'No due date')
      )
    ));
  });
}

async function updateProject(id, patch) {
  try {
    await api.projects.update(id, patch);
    await refreshProjectsSection();
  } catch (err) {
    showToast(`Project update failed: ${err.message}`, 'danger');
  }
}

function renderTasks() {
  const list = document.getElementById('home-tasks-list');
  list.innerHTML = '';
  state.tasks.forEach(task => {
    const project = state.projects.find(p => p.id === task.project_id);
    list.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, task.title || 'Task'),
        createElement('p', {}, task.description || '—'),
        project ? createElement('span', { class: 'pill secondary' }, project.name) : null
      ),
      createElement('div', { class: 'meta' },
        createElement('select', {
          onchange: e => updateTask(task.id, { status: e.target.value }),
        }, ...['todo', 'in-progress', 'blocked', 'done'].map(status => {
          const option = createElement('option', { value: status }, status);
          if (status === (task.status || 'todo')) option.selected = true;
          return option;
        })),
        createElement('small', {}, task.due_date ? formatDate(task.due_date) : 'No due date')
      )
    ));
  });
}

async function updateTask(id, patch) {
  try {
    await api.tasks.update(id, patch);
    await refreshProjectsSection();
  } catch (err) {
    showToast(`Task update failed: ${err.message}`, 'danger');
  }
}

function renderNotes() {
  const list = document.getElementById('home-notes-list');
  list.innerHTML = '';
  state.notes.forEach(note => {
    const project = state.projects.find(p => p.id === note.project_id);
    list.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, note.title || 'Note'),
        createElement('p', {}, (note.content || '').slice(0, 200))
      ),
      createElement('div', { class: 'meta' },
        project ? createElement('span', { class: 'pill secondary' }, project.name) : null,
        createElement('small', {}, formatDate(note.created_at, { withTime: true }))
      )
    ));
  });
}

function setupBudgetTab() {
  const form = document.getElementById('budget-import');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const formData = new FormData(form);
    const file = formData.get('file');
    if (!file || !file.size) {
      showToast('Select a CSV/XLSX file', 'warn');
      return;
    }
    try {
      const payload = await api.budget.import(formData);
      showToast(`Imported ${payload.imported} bills`, 'success');
      form.reset();
      await refreshBudget();
    } catch (err) {
      showToast(`Budget import failed: ${err.message}`, 'danger');
    }
  });
}

async function refreshBudget() {
  try {
    const summary = await api.budgetSummary();
    state.budget = summary;
    renderBudget();
  } catch (err) {
    showToast(`Budget load failed: ${err.message}`, 'danger');
  }
}

function renderBudget() {
  const summary = state.budget;
  if (!summary) return;
  const summaryBox = document.getElementById('budget-summary');
  summaryBox.innerHTML = '';
  summaryBox.appendChild(createElement('div', {}, `Open amount: $${Number(summary.totals.open_amount || 0).toFixed(2)}`));
  summaryBox.appendChild(createElement('div', {}, `Open bills: ${summary.totals.open_count}`));
  summaryBox.appendChild(createElement('div', {}, `Due in 7 days: $${Number(summary.totals.due_next_week || 0).toFixed(2)}`));
  (summary.categories || []).slice(0, 5).forEach(cat => {
    summaryBox.appendChild(createElement('div', {}, `${cat.category || 'uncategorized'} · $${Number(cat.total || 0).toFixed(2)}`));
  });

  const upcoming = document.getElementById('budget-upcoming');
  upcoming.innerHTML = '';
  (summary.upcoming || []).forEach(bill => {
    upcoming.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, bill.name || 'Bill'),
        createElement('p', {}, `$${Number(bill.amount || 0).toFixed(2)} · ${bill.category || 'uncategorized'}`)
      ),
      createElement('div', { class: 'meta' },
        createElement('span', { class: 'pill secondary' }, bill.status || 'scheduled'),
        createElement('small', {}, bill.due_date ? formatDate(bill.due_date) : 'No due date')
      )
    ));
  });

  const ledger = document.getElementById('budget-ledger');
  ledger.innerHTML = '';
  (summary.ledger || []).slice(0, 6).forEach(entry => {
    ledger.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, `$${Number(entry.amount || 0).toFixed(2)}`),
        createElement('p', {}, entry.notes || '')
      ),
      createElement('div', { class: 'meta' },
        createElement('small', {}, entry.paid_on ? formatDate(entry.paid_on) : '')
      )
    ));
  });
}

async function refreshSensors() {
  try {
    state.sensors = await api.sensors.list();
    renderSensors();
  } catch (err) {
    showToast(`Sensor load failed: ${err.message}`, 'danger');
  }
}

function renderSensors() {
  const grid = document.getElementById('sensor-grid');
  grid.innerHTML = '';
  const maxValue = state.sensors.reduce((max, sensor) => {
    const value = Number(sensor.last_value || 0);
    return Number.isFinite(value) && value > max ? value : max;
  }, 100);
  state.sensors.forEach(sensor => {
    const value = Number(sensor.last_value || 0);
    const gauge = createElement('div', { class: 'progress' },
      createElement('span', { class: 'label' }, `${sensor.name || 'Sensor'} · ${sensor.location || ''}`),
      createElement('div', { class: 'progress-track' },
        createElement('div', {
          class: 'progress-fill',
          style: `width: ${maxValue ? Math.min(100, Math.abs(value) / maxValue * 100).toFixed(1) : 0}%` },
          `${value.toFixed(1)} ${sensor.unit || ''}`
        )
      )
    );
    const card = createElement('div', { class: 'monky-panel' },
      createElement('h3', {}, sensor.name || 'Sensor'),
      createElement('p', { class: 'muted' }, sensor.description || ''),
      gauge,
      createElement('span', { class: sensor.status && sensor.status.toLowerCase() === 'ok' ? 'pill success' : 'pill warn' }, sensor.status || 'status'),
      createElement('small', {}, sensor.last_updated ? `Updated ${relativeTime(sensor.last_updated)}` : '')
    );
    grid.appendChild(card);
  });
}

function setupVaultTab() {
  document.getElementById('home-vault-unlock').addEventListener('submit', async e => {
    e.preventDefault();
    const pin = new FormData(e.target).get('pin');
    try {
      const result = await api.vault.auth(pin);
      if (result.ok) {
        state.vault.pin = pin;
        state.vault.unlocked = true;
        document.getElementById('home-vault-secure').hidden = false;
        await loadVaultItems();
        showToast('Vault unlocked', 'success');
      } else {
        showToast('Invalid PIN', 'danger');
      }
    } catch (err) {
      showToast(`Vault auth failed: ${err.message}`, 'danger');
    }
  });

  document.getElementById('home-vault-form').addEventListener('submit', async e => {
    e.preventDefault();
    if (!state.vault.unlocked) {
      showToast('Unlock vault first', 'warn');
      return;
    }
    const data = Object.fromEntries(new FormData(e.target).entries());
    data.pin = state.vault.pin;
    try {
      await api.vault.create(data);
      e.target.reset();
      await loadVaultItems();
      showToast('Secret stored', 'success');
    } catch (err) {
      showToast(`Vault error: ${err.message}`, 'danger');
    }
  });
}

async function loadVaultItems() {
  try {
    state.vault.items = await api.vault.list(state.vault.pin);
    renderVault();
  } catch (err) {
    showToast(`Vault load failed: ${err.message}`, 'danger');
  }
}

function renderVault() {
  const list = document.getElementById('home-vault-list');
  list.innerHTML = '';
  state.vault.items.forEach(item => {
    list.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, item.name || 'Secret'),
        createElement('p', {}, item.secret || ''),
        createElement('small', {}, item.category || '')
      ),
      createElement('div', { class: 'meta' },
        createElement('small', {}, relativeTime(item.updated_at || item.created_at)),
        createElement('button', { class: 'button secondary', onclick: () => deleteVaultItem(item.id) }, 'Delete')
      )
    ));
  });
}

async function deleteVaultItem(id) {
  if (!confirm('Delete secret?')) return;
  try {
    await api.vault.remove(id, state.vault.pin);
    await loadVaultItems();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, 'danger');
  }
}

function setupDevTab() {
  document.getElementById('home-dev-refresh').addEventListener('click', refreshDev);
  document.getElementById('home-dev-save').addEventListener('click', saveConfig);
  document.getElementById('home-dev-reload').addEventListener('click', reloadConfig);
  document.getElementById('home-dev-backup').addEventListener('click', async () => {
    try {
      const res = await api.dev.backup();
      showToast(`Backup created: ${res.backup}`, 'success');
    } catch (err) {
      showToast(`Backup failed: ${err.message}`, 'danger');
    }
  });
  document.getElementById('home-dev-restore').addEventListener('submit', async e => {
    e.preventDefault();
    const name = new FormData(e.target).get('name');
    try {
      await api.dev.restore(name);
      showToast('Restore triggered', 'success');
    } catch (err) {
      showToast(`Restore failed: ${err.message}`, 'danger');
    }
  });
}

async function refreshDev() {
  try {
    const [status, configData] = await Promise.all([api.dev.status(), api.settings.config()]);
    renderDevStatus(status);
    renderDevConfig(configData);
  } catch (err) {
    showToast(`Dev load failed: ${err.message}`, 'danger');
  }
}

function renderDevStatus(status) {
  const container = document.getElementById('home-dev-status');
  container.innerHTML = '';
  container.appendChild(createElement('div', {}, `Sensors: ${status.sensor_count}`));
  container.appendChild(createElement('div', {}, `Open tasks: ${status.open_tasks}`));
  container.appendChild(createElement('div', {}, `Bills: ${status.bill_count}`));
  container.appendChild(createElement('div', {}, `DB path: ${status.db_path}`));
  if (status.storage) {
    container.appendChild(createElement('div', {}, `Work storage: ${status.storage.work || ''}`));
    container.appendChild(createElement('div', {}, `Home storage: ${status.storage.home || ''}`));
  }
}

function renderDevConfig(configData) {
  document.getElementById('home-dev-config').value = JSON.stringify(configData, null, 2);
}

async function saveConfig() {
  try {
    const parsed = JSON.parse(document.getElementById('home-dev-config').value);
    await api.settings.update(parsed);
    showToast('Config saved', 'success');
  } catch (err) {
    showToast(`Config save failed: ${err.message}`, 'danger');
  }
}

async function reloadConfig() {
  try {
    const configData = await api.settings.config();
    renderDevConfig(configData);
    showToast('Config reloaded', 'success');
  } catch (err) {
    showToast(`Config load failed: ${err.message}`, 'danger');
  }
}

window.addEventListener('beforeunload', () => {
  if (clockInterval) clearInterval(clockInterval);
});
