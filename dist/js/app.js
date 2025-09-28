
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
