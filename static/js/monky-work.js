import { api, showToast, formatDate, relativeTime, createElement, renderSources, bindCopyButtons } from './monky-common.js';

const TAB_NAMES = ['home', 'assistant', 'projects', 'kpi', 'data', 'vault', 'dev'];

const state = {
  env: {},
  tabsLoaded: Object.fromEntries(TAB_NAMES.map(tab => [tab, false])),
  assistant: {
    providers: [],
    provider: 'local',
    threads: [],
    threadId: null,
    messages: [],
  },
  projects: [],
  tasks: [],
  notes: [],
  kpi: [],
  ragDocs: [],
  assets: [],
  vault: { pin: '', unlocked: false, items: [] },
  dashboard: null,
};

const elements = {
  nav: document.getElementById('monky-nav'),
  topMeta: document.getElementById('top-meta'),
  providerPill: document.getElementById('assistant-provider-pill'),
  clock: document.getElementById('clock'),
  tabs: Array.from(document.querySelectorAll('.monky-tab')),
};

let clockInterval;

document.addEventListener('DOMContentLoaded', () => {
  setupNav();
  setupClock();
  loadEnv();
  activateTab('home');
  setupProjectsTab();
  setupKpiTab();
  setupDataTab();
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
  elements.tabs.forEach(tab => tab.classList.toggle('active', tab.id === `tab-${tabId}`));
  if (!state.tabsLoaded[tabId]) {
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
      case 'kpi':
        refreshKpi();
        break;
      case 'data':
        refreshData();
        break;
      case 'vault':
        // no auto load until unlocked
        break;
      case 'dev':
        refreshDev();
        break;
      default:
        break;
    }
    state.tabsLoaded[tabId] = true;
  }
}

async function loadHome() {
  try {
    const summary = await api.dashboard('work');
    state.dashboard = summary;
    renderHome(summary);
    state.tabsLoaded.home = true;
  } catch (err) {
    showToast(`Home load failed: ${err.message}`, 'danger');
  }
}

function renderHome(summary) {
  const statsEl = document.getElementById('home-stats');
  statsEl.innerHTML = '';
  const cards = [
    { label: 'Active Projects', value: summary.projects ? summary.projects.filter(p => (p.status || 'active') !== 'done').length : 0 },
    { label: 'Open Tasks', value: summary.tasks ? summary.tasks.filter(t => (t.status || 'todo') !== 'done').length : 0 },
    { label: 'Knowledge Assets', value: summary.data_assets ? summary.data_assets.length : 0 },
    { label: 'KPI Datasets', value: summary.kpi ? summary.kpi.length : 0 },
  ];
  cards.forEach(card => {
    const el = createElement('div', { class: 'stat-card' },
      createElement('span', { class: 'muted' }, card.label),
      createElement('strong', {}, String(card.value))
    );
    statsEl.appendChild(el);
  });

  renderKpiVisuals(summary.kpi || []);

  const projectsList = document.getElementById('home-projects-list');
  projectsList.innerHTML = '';
  (summary.projects || []).slice(0, 4).forEach(project => {
    projectsList.appendChild(
      createElement('li', { class: 'list-item' },
        createElement('div', { class: 'info' },
          createElement('h4', {}, project.name || 'Untitled Project'),
          createElement('p', {}, project.description || '—')
        ),
        createElement('div', { class: 'meta' },
          createElement('span', { class: 'pill secondary' }, project.status || 'active'),
          createElement('small', {}, project.due_date ? `Due ${formatDate(project.due_date)}` : 'No due date')
        )
      )
    );
  });

  const tasksList = document.getElementById('home-tasks-list');
  tasksList.innerHTML = '';
  (summary.tasks || []).slice(0, 6).forEach(task => {
    tasksList.appendChild(
      createElement('li', { class: 'list-item' },
        createElement('div', { class: 'info' },
          createElement('h4', {}, task.title || 'Untitled Task'),
          createElement('p', {}, task.description || '—')
        ),
        createElement('div', { class: 'meta' },
          createElement('span', { class: 'pill secondary' }, task.status || 'todo'),
          createElement('small', {}, task.due_date ? formatDate(task.due_date) : 'No due date')
        )
      )
    );
  });

  const notesList = document.getElementById('home-notes-list');
  notesList.innerHTML = '';
  (summary.notes || []).slice(0, 6).forEach(note => {
    notesList.appendChild(
      createElement('li', { class: 'list-item' },
        createElement('div', { class: 'info' },
          createElement('h4', {}, note.title || 'Note'),
          createElement('p', {}, (note.content || '').slice(0, 180))
        ),
        createElement('div', { class: 'meta' },
          createElement('small', {}, formatDate(note.created_at, { withTime: true }))
        )
      )
    );
  });

  updateTopMeta(summary);
}

function renderKpiVisuals(datasets) {
  const container = document.getElementById('home-kpi-visuals');
  if (!container) return;
  container.innerHTML = '';
  datasets.slice(0, 3).forEach(dataset => {
    const card = createElement('div', { class: 'kpi-card' },
      createElement('strong', {}, dataset.name || 'Dataset'),
      createElement('span', { class: 'muted' }, dataset.source || '')
    );

    const metrics = dataset.metrics?.metrics || dataset.metrics || {};
    const entries = Object.entries(metrics).slice(0, 4);
    const maxValue = entries.reduce((acc, [, stats]) => {
      const value = Number(stats?.avg ?? stats?.sum ?? stats?.max ?? 0);
      return Number.isFinite(value) && value > acc ? value : acc;
    }, 0);

    entries.forEach(([name, stats]) => {
      const value = Number(stats?.avg ?? stats?.sum ?? stats?.max ?? 0);
      const track = createElement('div', { class: 'progress' },
        createElement('span', { class: 'label' }, name),
        createElement('div', { class: 'progress-track' },
          createElement('div', {
            class: 'progress-fill',
            style: `width: ${maxValue ? Math.min(100, (value / maxValue) * 100).toFixed(1) : 0}%`,
          }, formatMetricValue(value))
        )
      );
      card.appendChild(track);
    });

    container.appendChild(card);
  });
}

function formatMetricValue(value) {
  if (!Number.isFinite(value)) return '0';
  if (Math.abs(value) >= 1000) return `${value.toFixed(0)}`;
  return value.toFixed(2);
}

function updateTopMeta(summary) {
  elements.topMeta.innerHTML = '';
  const pills = [];
  if (summary.projects) {
    const active = summary.projects.filter(p => (p.status || 'active') !== 'done').length;
    pills.push(`${active} active projects`);
  }
  if (summary.tasks) {
    const open = summary.tasks.filter(t => (t.status || 'todo') !== 'done').length;
    pills.push(`${open} open tasks`);
  }
  if (summary.kpi) {
    pills.push(`${summary.kpi.length} KPI datasets`);
  }
  pills.forEach(text => {
    elements.topMeta.appendChild(createElement('span', {}, text));
  });
}

async function initAssistant() {
  if (state.tabsLoaded.assistant) return;
  try {
    const providerData = await api.assistant.providers();
    state.assistant.providers = providerData.providers || [];
    state.assistant.provider = providerData.default || (state.assistant.providers[0]?.id || 'local');
    populateProviderSelects();
    await ensureThreadExists();
    await refreshThreads(state.assistant.threadId);
    setupAssistantEvents();
    state.tabsLoaded.assistant = true;
  } catch (err) {
    showToast(`Assistant init failed: ${err.message}`, 'danger');
  }
}

function syncModalProvider() {
  const modalSelect = document.getElementById('modal-chat-provider');
  const modelInput = document.getElementById('modal-chat-model');
  if (!modalSelect || !modelInput) return;
  modalSelect.value = state.assistant.provider;
  const selected = modalSelect.selectedOptions[0];
  if (selected) {
    modelInput.dataset.model = selected.dataset.model || '';
    modelInput.placeholder = selected.dataset.model
      ? `Model ${selected.dataset.model}`
      : 'Model override (optional)';
  }
}

function populateProviderSelects() {
  const selects = [
    document.getElementById('assistant-provider'),
    document.getElementById('modal-chat-provider'),
  ];
  selects.forEach(select => {
    if (!select) return;
    select.innerHTML = '';
    state.assistant.providers.forEach(provider => {
      const option = createElement('option', { value: provider.id }, `${provider.label}${provider.has_key ? '' : ' · simulated'}`);
      if (provider.id === state.assistant.provider) {
        option.selected = true;
        option.dataset.model = provider.model || '';
      }
      select.appendChild(option);
    });
  });
  elements.providerPill.textContent = `Provider · ${state.assistant.provider}`;
  syncModalProvider();
}

async function ensureThreadExists() {
  const threads = await api.assistant.threads();
  if (!threads.length) {
    const created = await api.assistant.createThread({ title: 'Main Thread' });
    state.assistant.threadId = created.id;
  } else {
    state.assistant.threadId = threads[0].id;
  }
}

async function refreshThreads(selectId) {
  const threads = await api.assistant.threads();
  state.assistant.threads = threads;
  if (!threads.length) {
    await ensureThreadExists();
    return refreshThreads(state.assistant.threadId);
  }
  if (!selectId) {
    selectId = state.assistant.threadId || threads[0].id;
  }
  renderAssistantThreads(selectId);
  await loadMessages(selectId);
}

function renderAssistantThreads(activeId) {
  const list = document.getElementById('assistant-thread-list');
  list.innerHTML = '';
  state.assistant.threads.forEach(thread => {
    const btn = createElement('button', { class: thread.id === activeId ? 'active' : '' },
      createElement('div', {}, thread.title || `Thread ${thread.id}`),
      createElement('small', {}, thread.preview ? thread.preview.slice(0, 80) : 'No messages')
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
    showToast(`Load messages failed: ${err.message}`, 'danger');
  }
}

function renderMessages() {
  const container = document.getElementById('assistant-messages');
  container.innerHTML = '';
  state.assistant.messages.forEach(message => {
    const messageEl = createElement('div', { class: `chat-message ${message.role}` });
    const header = createElement('header', {},
      createElement('span', {}, message.role === 'assistant' ? 'MONKY' : 'You'),
      createElement('small', {}, `${formatDate(message.created_at, { withTime: true })}`)
    );
    const controls = createElement('div', { class: 'chat-actions' });
    const copyBtn = createElement('button', { class: 'button secondary', 'data-copy': message.content }, 'Copy');
    controls.appendChild(copyBtn);
    header.appendChild(controls);

    const body = createElement('div');
    const formatted = formatMarkdown(message.content || '');
    body.innerHTML = formatted;
    const sourcesEl = createElement('div', { class: 'chat-sources' });
    renderSources(sourcesEl, message.sources);

    messageEl.appendChild(header);
    messageEl.appendChild(body);
    if (message.sources && message.sources.length) {
      messageEl.appendChild(sourcesEl);
    }
    container.appendChild(messageEl);
  });
  container.scrollTop = container.scrollHeight;
  bindCopyButtons(container);
}

function renderModalMessages() {
  const container = document.getElementById('modal-chat-messages');
  if (!container) return;
  container.innerHTML = '';
  state.assistant.messages.slice(-8).forEach(message => {
    container.appendChild(
      createElement('div', { class: `chat-message ${message.role}` },
        createElement('header', {},
          createElement('span', {}, message.role === 'assistant' ? 'MONKY' : 'You'),
          createElement('small', {}, relativeTime(message.created_at))
        ),
        createElement('div', {}, message.content)
      )
    );
  });
  container.scrollTop = container.scrollHeight;
}

function setupAssistantEvents() {
  document.getElementById('assistant-provider').addEventListener('change', e => {
    state.assistant.provider = e.target.value;
    syncModalProvider();
    elements.providerPill.textContent = `Provider · ${state.assistant.provider}`;
  });
  document.getElementById('modal-chat-provider').addEventListener('change', e => {
    state.assistant.provider = e.target.value;
    document.getElementById('assistant-provider').value = state.assistant.provider;
    syncModalProvider();
    elements.providerPill.textContent = `Provider · ${state.assistant.provider}`;
  });
  document.getElementById('assistant-send').addEventListener('click', () => sendAssistantMessage('main'));
  document.getElementById('assistant-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      sendAssistantMessage('main');
    }
  });
  document.getElementById('assistant-refresh').addEventListener('click', () => loadMessages(state.assistant.threadId));
  document.getElementById('assistant-new-thread').addEventListener('click', async () => {
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
  const textarea = source === 'main' ? document.getElementById('assistant-input') : document.getElementById('modal-chat-input');
  const providerSelect = source === 'main' ? document.getElementById('assistant-provider') : document.getElementById('modal-chat-provider');
  const modelInput = source === 'main' ? null : document.getElementById('modal-chat-model');
  const message = textarea.value.trim();
  if (!message) {
    showToast('Message cannot be empty', 'warn');
    return;
  }
  try {
    await api.assistant.send({
      thread_id: state.assistant.threadId,
      provider: providerSelect.value,
      model: modelInput
        ? modelInput.value.trim() || modelInput.dataset.model || undefined
        : undefined,
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
  const paragraphs = escaped.split(/\n\n+/).map(block => {
    if (block.startsWith('```')) {
      const code = block.replace(/```/g, '').trim();
      return `<pre><code>${code}</code></pre>`;
    }
    return `<p>${block.replace(/\n/g, '<br />')}</p>`;
  });
  return paragraphs.join('');
}

function setupProjectsTab() {
  const subtabs = document.getElementById('projects-subtabs');
  if (!subtabs) return;
  subtabs.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      subtabs.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b === btn ? 'true' : 'false'));
      document.querySelectorAll('#tab-projects .section-block').forEach(section => {
        if (section.id === `${btn.dataset.target}-section`) {
          section.hidden = false;
        } else {
          section.hidden = true;
        }
      });
    });
  });

  document.getElementById('project-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.projects.create(data);
      e.target.reset();
      showToast('Project saved', 'success');
      await refreshProjectsSection();
    } catch (err) {
      showToast(`Project error: ${err.message}`, 'danger');
    }
  });

  document.getElementById('task-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.tasks.create(data);
      e.target.reset();
      showToast('Task saved', 'success');
      await refreshProjectsSection();
    } catch (err) {
      showToast(`Task error: ${err.message}`, 'danger');
    }
  });

  document.getElementById('note-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.notes.create(data);
      e.target.reset();
      showToast('Note saved', 'success');
      await refreshProjectsSection();
    } catch (err) {
      showToast(`Note error: ${err.message}`, 'danger');
    }
  });
}

async function refreshProjectsSection() {
  try {
    const [projects, tasks, notes] = await Promise.all([
      api.projects.list(),
      api.tasks.list(),
      api.notes.list(),
    ]);
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
  const selects = [document.getElementById('task-project-select'), document.getElementById('note-project-select')];
  selects.forEach(select => {
    select.innerHTML = '<option value="">No project</option>';
    state.projects.forEach(project => {
      const option = createElement('option', { value: project.id }, project.name || `Project ${project.id}`);
      select.appendChild(option);
    });
  });
}

function renderProjects() {
  const list = document.getElementById('projects-list');
  list.innerHTML = '';
  state.projects.forEach(project => {
    const item = createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, project.name || 'Untitled Project'),
        createElement('p', {}, project.description || '—'),
        createElement('div', {}, (project.tags || '').split(',').filter(Boolean).map(tag => createElement('span', { class: 'tag' }, tag.trim())))
      ),
      createElement('div', { class: 'meta' },
        createElement('select', { value: project.status || 'active', onchange: e => updateProject(project.id, { status: e.target.value }) },
          ...['active', 'planning', 'blocked', 'done'].map(status => {
            const option = createElement('option', { value: status }, status);
            if (status === (project.status || 'active')) option.selected = true;
            return option;
          })
        ),
        createElement('small', {}, project.due_date ? `Due ${formatDate(project.due_date)}` : 'No due date'),
        createElement('button', { class: 'button secondary', onclick: () => deleteProject(project.id) }, 'Archive')
      )
    );
    list.appendChild(item);
  });
}

async function updateProject(id, patch) {
  try {
    await api.projects.update(id, patch);
    await refreshProjectsSection();
  } catch (err) {
    showToast(`Update failed: ${err.message}`, 'danger');
  }
}

async function deleteProject(id) {
  if (!confirm('Archive this project?')) return;
  try {
    await api.projects.remove(id);
    await refreshProjectsSection();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, 'danger');
  }
}

function renderTasks() {
  const list = document.getElementById('tasks-list');
  list.innerHTML = '';
  state.tasks.forEach(task => {
    const project = state.projects.find(p => p.id === task.project_id);
    const item = createElement('li', { class: 'list-item' },
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
        createElement('small', {}, task.due_date ? formatDate(task.due_date) : 'No due date'),
        createElement('button', { class: 'button secondary', onclick: () => deleteTask(task.id) }, 'Delete')
      )
    );
    list.appendChild(item);
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

async function deleteTask(id) {
  if (!confirm('Delete task?')) return;
  try {
    await api.tasks.remove(id);
    await refreshProjectsSection();
  } catch (err) {
    showToast(`Task delete failed: ${err.message}`, 'danger');
  }
}

function renderNotes() {
  const list = document.getElementById('notes-list');
  list.innerHTML = '';
  state.notes.forEach(note => {
    const project = state.projects.find(p => p.id === note.project_id);
    const item = createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, note.title || 'Note'),
        createElement('p', {}, (note.content || '').slice(0, 220))
      ),
      createElement('div', { class: 'meta' },
        project ? createElement('span', { class: 'pill secondary' }, project.name) : null,
        createElement('small', {}, formatDate(note.created_at, { withTime: true })),
        createElement('button', { class: 'button secondary', onclick: () => deleteNote(note.id) }, 'Delete')
      )
    );
    list.appendChild(item);
  });
}

async function deleteNote(id) {
  if (!confirm('Delete note?')) return;
  try {
    await api.notes.remove(id);
    await refreshProjectsSection();
  } catch (err) {
    showToast(`Note delete failed: ${err.message}`, 'danger');
  }
}

function setupKpiTab() {
  const form = document.getElementById('kpi-upload');
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const formData = new FormData(form);
    if (!formData.get('file')) {
      showToast('Select a file to upload', 'warn');
      return;
    }
    try {
      await api.kpi.upload(formData);
      showToast('KPI uploaded', 'success');
      form.reset();
      await refreshKpi();
    } catch (err) {
      showToast(`KPI upload failed: ${err.message}`, 'danger');
    }
  });
}

async function refreshKpi() {
  try {
    state.kpi = await api.kpi.list();
    renderKpi();
    renderKpiVisuals(state.kpi);
  } catch (err) {
    showToast(`KPI load failed: ${err.message}`, 'danger');
  }
}

function renderKpi() {
  const container = document.getElementById('kpi-datasets');
  container.innerHTML = '';
  state.kpi.forEach(dataset => {
    const metrics = dataset.metrics?.metrics || dataset.metrics || {};
    const preview = dataset.metrics?.preview || [];
    const card = createElement('div', { class: 'monky-panel' },
      createElement('h3', {}, dataset.name || 'Dataset'),
      createElement('p', { class: 'muted' }, dataset.source || ''),
      createElement('div', {}, Object.entries(metrics).slice(0, 3).map(([key, value]) => {
        const avg = value.avg ?? value.mean ?? value.average;
        return createElement('div', {}, `${key}: avg ${avg !== undefined ? Number(avg).toFixed(2) : '—'}`);
      })),
      createElement('button', { class: 'button secondary', onclick: () => removeKpi(dataset.id) }, 'Delete')
    );
    if (preview && preview.length) {
      const list = createElement('ul', { class: 'list' });
      preview.slice(0, 3).forEach(row => {
        list.appendChild(createElement('li', { class: 'list-item' }, JSON.stringify(row)));
      });
      card.appendChild(list);
    }
    container.appendChild(card);
  });
}

async function removeKpi(id) {
  if (!confirm('Delete KPI dataset?')) return;
  try {
    await api.kpi.remove(id);
    await refreshKpi();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, 'danger');
  }
}

function setupDataTab() {
  document.getElementById('rag-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.rag.create(data);
      showToast('Document added', 'success');
      e.target.reset();
      await refreshData();
    } catch (err) {
      showToast(`RAG error: ${err.message}`, 'danger');
    }
  });
  document.getElementById('asset-form').addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target).entries());
    try {
      await api.dataAssets.create(data);
      showToast('Asset added', 'success');
      e.target.reset();
      await refreshData();
    } catch (err) {
      showToast(`Asset error: ${err.message}`, 'danger');
    }
  });
  document.getElementById('rag-search').addEventListener('submit', async e => {
    e.preventDefault();
    const query = new FormData(e.target).get('query');
    if (!query) return;
    try {
      const results = await api.rag.search(query);
      const list = document.getElementById('rag-search-results');
      list.innerHTML = '';
      results.forEach(entry => {
        list.appendChild(createElement('li', { class: 'list-item' },
          createElement('div', { class: 'info' },
            createElement('h4', {}, entry.title || 'Result'),
            createElement('p', {}, entry.excerpt || '')
          )
        ));
      });
    } catch (err) {
      showToast(`Search failed: ${err.message}`, 'danger');
    }
  });
}

async function refreshData() {
  try {
    const [docs, assets] = await Promise.all([api.rag.list(), api.dataAssets.list()]);
    state.ragDocs = docs;
    state.assets = assets;
    renderRagDocs();
    renderAssets();
  } catch (err) {
    showToast(`Data load failed: ${err.message}`, 'danger');
  }
}

function renderRagDocs() {
  const list = document.getElementById('rag-list');
  list.innerHTML = '';
  state.ragDocs.forEach(doc => {
    list.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, doc.title || 'Document'),
        createElement('p', {}, (doc.content || '').slice(0, 200)),
        createElement('small', {}, `Tags: ${doc.tags || '—'}`)
      ),
      createElement('div', { class: 'meta' },
        createElement('small', {}, formatDate(doc.created_at, { withTime: true })),
        createElement('button', { class: 'button secondary', onclick: () => removeRagDoc(doc.id) }, 'Delete')
      )
    ));
  });
}

async function removeRagDoc(id) {
  if (!confirm('Delete document?')) return;
  try {
    await api.rag.remove(id);
    await refreshData();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`, 'danger');
  }
}

function renderAssets() {
  const list = document.getElementById('asset-list');
  list.innerHTML = '';
  state.assets.forEach(asset => {
    list.appendChild(createElement('li', { class: 'list-item' },
      createElement('div', { class: 'info' },
        createElement('h4', {}, asset.title || 'Asset'),
        createElement('p', {}, asset.description || '—'),
        createElement('small', {}, asset.tags ? `Tags: ${asset.tags}` : '')
      ),
      createElement('div', { class: 'meta' },
        createElement('small', {}, relativeTime(asset.added_at)),
        createElement('button', { class: 'button secondary', onclick: () => removeAsset(asset.id) }, 'Delete')
      )
    ));
  });
}

async function removeAsset(id) {
  if (!confirm('Remove asset?')) return;
  try {
    await api.dataAssets.remove(id);
    await refreshData();
  } catch (err) {
    showToast(`Remove failed: ${err.message}`, 'danger');
  }
}

function setupVaultTab() {
  const unlockForm = document.getElementById('vault-unlock');
  const vaultForm = document.getElementById('vault-form');
  unlockForm.addEventListener('submit', async e => {
    e.preventDefault();
    const pin = new FormData(unlockForm).get('pin');
    try {
      const res = await api.vault.auth(pin);
      if (res.ok) {
        state.vault.pin = pin;
        state.vault.unlocked = true;
        document.getElementById('vault-secure').hidden = false;
        await loadVaultItems();
        showToast('Vault unlocked', 'success');
      } else {
        showToast('Invalid PIN', 'danger');
      }
    } catch (err) {
      showToast(`Vault auth failed: ${err.message}`, 'danger');
    }
  });
  vaultForm.addEventListener('submit', async e => {
    e.preventDefault();
    if (!state.vault.unlocked) {
      showToast('Unlock vault first', 'warn');
      return;
    }
    const data = Object.fromEntries(new FormData(vaultForm).entries());
    data.pin = state.vault.pin;
    try {
      await api.vault.create(data);
      vaultForm.reset();
      await loadVaultItems();
      showToast('Secret stored', 'success');
    } catch (err) {
      showToast(`Vault error: ${err.message}`, 'danger');
    }
  });
}

async function loadVaultItems() {
  try {
    const items = await api.vault.list(state.vault.pin);
    state.vault.items = items;
    renderVault();
  } catch (err) {
    showToast(`Vault load failed: ${err.message}`, 'danger');
  }
}

function renderVault() {
  const list = document.getElementById('vault-list');
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
    showToast(`Vault delete failed: ${err.message}`, 'danger');
  }
}

function setupDevTab() {
  document.getElementById('dev-refresh').addEventListener('click', refreshDev);
  document.getElementById('dev-save-config').addEventListener('click', saveConfig);
  document.getElementById('dev-reload-config').addEventListener('click', refreshDevConfig);
  document.getElementById('dev-backup').addEventListener('click', async () => {
    try {
      const res = await api.dev.backup();
      showToast(`Backup created: ${res.backup}`, 'success');
    } catch (err) {
      showToast(`Backup failed: ${err.message}`, 'danger');
    }
  });
  document.getElementById('dev-restore').addEventListener('submit', async e => {
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
    state.tabsLoaded.dev = true;
  } catch (err) {
    showToast(`Dev load failed: ${err.message}`, 'danger');
  }
}

async function refreshDevConfig() {
  try {
    const configData = await api.settings.config();
    renderDevConfig(configData);
    showToast('Config reloaded', 'success');
  } catch (err) {
    showToast(`Config load failed: ${err.message}`, 'danger');
  }
}

function renderDevStatus(status) {
  const container = document.getElementById('dev-status');
  container.innerHTML = '';
  container.appendChild(createElement('div', {}, `Sensors: ${status.sensor_count}`));
  container.appendChild(createElement('div', {}, `Open tasks: ${status.open_tasks}`));
  container.appendChild(createElement('div', {}, `Bills tracked: ${status.bill_count}`));
  container.appendChild(createElement('div', {}, `DB path: ${status.db_path}`));
  if (status.storage) {
    container.appendChild(createElement('div', {}, `Work storage: ${status.storage.work || ''}`));
    container.appendChild(createElement('div', {}, `Home storage: ${status.storage.home || ''}`));
  }
}

function renderDevConfig(configData) {
  const textarea = document.getElementById('dev-config');
  textarea.value = JSON.stringify(configData, null, 2);
}

async function saveConfig() {
  try {
    const textarea = document.getElementById('dev-config');
    const parsed = JSON.parse(textarea.value);
    await api.settings.update(parsed);
    showToast('Config saved', 'success');
  } catch (err) {
    showToast(`Config save failed: ${err.message}`, 'danger');
  }
}

function setupAssistantModal() {
  const modal = document.getElementById('assistant-modal');
  const openBtn = document.getElementById('assistant-open-modal');
  const closeBtn = document.getElementById('assistant-modal-close');
  const sendBtn = document.getElementById('modal-chat-send');
  const input = document.getElementById('modal-chat-input');

  if (openBtn) {
    openBtn.addEventListener('click', () => {
      modal.classList.remove('hidden');
      renderModalMessages();
      syncModalProvider();
      input.focus();
    });
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      modal.classList.add('hidden');
    });
  }
  if (sendBtn) {
    sendBtn.addEventListener('click', () => sendAssistantMessage('modal'));
  }
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        modal.classList.add('hidden');
      }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        sendAssistantMessage('modal');
      }
    });
  }
  modal.addEventListener('click', e => {
    if (e.target === modal) {
      modal.classList.add('hidden');
    }
  });
}

window.addEventListener('beforeunload', () => {
  if (clockInterval) clearInterval(clockInterval);
});
