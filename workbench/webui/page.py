"""单页界面：标准库 + 原生 JS，没有构建步骤，手机也能用。"""

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>内容生产工作台</title>
<style>
  :root { --line:#e5e7eb; --ink:#111827; --muted:#6b7280; --bg:#f8fafc; --ok:#0f766e; --warn:#b45309; --bad:#b91c1c; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
         color:var(--ink); background:var(--bg); }
  header { position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line); padding:12px 16px; z-index:9; }
  h1 { font-size:16px; margin:0 0 6px; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { font-size:12px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); background:#fff; color:var(--muted); }
  .chip.on { color:var(--ok); border-color:#99f6e4; background:#f0fdfa; }
  .chip.off { color:var(--bad); border-color:#fecaca; background:#fef2f2; }
  main { max-width:960px; margin:0 auto; padding:16px; display:grid; gap:16px; }
  section { background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px; }
  section h2 { font-size:14px; margin:0 0 10px; color:var(--muted); font-weight:600; letter-spacing:.02em; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  td, th { border-bottom:1px solid var(--line); padding:6px 4px; text-align:left; vertical-align:middle; }
  input[type=text], select, textarea { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:8px; font:inherit; background:#fff; }
  textarea { min-height:60px; }
  button { font:inherit; padding:6px 12px; border-radius:8px; border:1px solid var(--line); background:#fff; cursor:pointer; }
  button.primary { background:#111827; color:#fff; border-color:#111827; }
  button.ghost { color:var(--muted); }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .card { border:1px solid var(--line); border-radius:10px; padding:10px; margin-bottom:10px; }
  .card .ask { font-weight:600; }
  .meta { color:var(--muted); font-size:12px; }
  .pill { font-size:12px; padding:1px 7px; border-radius:999px; background:#f3f4f6; color:var(--muted); }
  .pill.ok { background:#ecfdf5; color:var(--ok); } .pill.warn { background:#fffbeb; color:var(--warn); } .pill.bad { background:#fef2f2; color:var(--bad); }
  ul { margin:6px 0 0; padding-left:18px; } li { margin:2px 0; }
  .hint { color:var(--muted); font-size:12px; margin-top:6px; }
  .err { color:var(--bad); font-size:13px; }
  .hidden { display:none; }
</style>
</head>
<body>
<header>
  <h1>内容生产工作台 <span class="meta" id="mode"></span></h1>
  <div class="chips" id="caps"></div>
</header>

<main>
  <section>
    <h2>现在缺什么</h2>
    <div id="doctor" class="meta">加载中…</div>
    <div class="hint">必填项缺失只影响对应能力，不会让整个系统假装能跑。</div>
  </section>

  <section>
    <h2>配置</h2>
    <table><tbody id="configRows"></tbody></table>
    <div class="hint">口令类字段只显示“已设置”，不会被页面回显；改完立即生效，无需重启（下次读取配置时生效）。</div>
  </section>

  <section>
    <h2>开跑一题</h2>
    <div class="row">
      <input type="text" id="topic" placeholder="要做什么题（一句话）" style="flex:1 1 240px">
      <select id="form" style="flex:0 0 140px">
        <option value="article_first">先图文后视频</option>
        <option value="video_first">先视频后图文</option>
      </select>
      <label class="meta"><input type="checkbox" id="withVault" checked> 接仓库取材</label>
      <button class="primary" id="runBtn">开跑</button>
    </div>
    <div id="runs" class="meta" style="margin-top:10px"></div>
  </section>

  <section>
    <h2>闸门卡片（票拟）</h2>
    <div id="cards" class="meta">加载中…</div>
    <div id="receipt" class="hidden card" style="margin-top:10px">
      <div class="ask">先确认我的理解</div>
      <div id="receiptEcho" style="margin:6px 0"></div>
      <div class="row">
        <button class="primary" id="confirmYes">对，就这么办</button>
        <button class="ghost" id="confirmNo">不对，作废</button>
      </div>
    </div>
  </section>

  <section>
    <h2>决策台账</h2>
    <div id="ledger" class="meta">加载中…</div>
  </section>

  <section>
    <h2>审计（最近 40 条）</h2>
    <pre id="audit" class="meta" style="white-space:pre-wrap; font-size:12px"></pre>
  </section>
</main>

<script>
const store = { token: localStorage.getItem('wb_token') || '', pending: null };

function q(sel, root=document){ return root.querySelector(sel); }
function el(tag, attrs={}, ...kids){
  const node = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v);
  }
  kids.flat().forEach(k => node.append(k?.nodeType ? k : document.createTextNode(k)));
  return node;
}
async function api(path, opts={}) {
  const headers = Object.assign({'Content-Type':'application/json'}, opts.headers||{});
  if (store.token) headers['X-WB-Token'] = store.token;
  const res = await fetch(path, Object.assign({}, opts, {headers}));
  if (res.status === 401) {
    const t = prompt('需要口令（config.toml 里 [webui] token）');
    if (t) { store.token = t; localStorage.setItem('wb_token', t); return api(path, opts); }
    throw new Error('未认证');
  }
  const data = await res.json().catch(()=>({error:'响应不是 JSON'}));
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

async function loadStatus() {
  try {
    const s = await api('/api/status');
    q('#mode').textContent = `· 发布模式 ${s.mode} · 平台 ${s.platforms.join('/')}`;
    const caps = q('#caps'); caps.innerHTML = '';
    const label = {ingest_materials:'取材', retrieval:'检索', generation:'生成', writeback:'写回', card_push_feishu:'飞书卡片'};
    for (const [k,v] of Object.entries(s.capabilities))
      caps.append(el('span', {class:'chip ' + (v?'on':'off'), text:(v?'可用 ':'不可用 ') + (label[k]||k)}));
    const doc = q('#doctor'); doc.innerHTML = '';
    if (!s.items.length) doc.append('必填项齐全 ✅');
    for (const it of s.items) {
      doc.append(el('div', {}, el('span', {class:'pill ' + (it.kind==='required'?'bad':'warn'), text: it.kind==='required'?'必填':'可选'}),
        ' ', it.key + '：' + it.reason));
    }
  } catch (e) { q('#caps').textContent = '状态不可用：' + e.message; }
}

async function loadConfig() {
  try {
    const {rows} = await api('/api/config');
    const tbody = q('#configRows'); tbody.innerHTML = '';
    for (const r of rows) {
      const input = el('input', {type:'text', placeholder: r.secret ? (r.filled ? r.value : '（未设置）') : '（未填）', value: r.secret ? '' : (r.value||'')});
      const btn = el('button', {onclick: async () => {
        const value = input.value.trim();
        if (r.secret && !value) { alert('这一项留空表示不改动；要清空请填 CLEAR'); return; }
        try { await api('/api/config', {method:'POST', body: JSON.stringify({key:r.key, value: value === 'CLEAR' ? '' : value})});
              alert('已写入 ' + r.key + '（口令类字段需重启服务或下次读取时生效）'); loadStatus(); loadConfig(); }
        catch (e) { alert(e.message); }
      }, text:'保存'});
      tbody.append(el('tr', {},
        el('td', {class:'meta', text:r.key}),
        el('td', {}, input),
        el('td', {style:'width:70px'}, btn)));
    }
  } catch (e) { q('#configRows').innerHTML = '<tr><td class="err">' + e.message + '</td></tr>'; }
}

async function loadRuns() {
  try {
    const {runs} = await api('/api/runs');
    const box = q('#runs'); box.innerHTML = '';
    if (!runs.length) { box.textContent = '还没有跑过。'; return; }
    for (const r of runs.slice().reverse()) {
      const pill = r.state === 'done' ? 'ok' : (r.state === 'failed' ? 'bad' : 'warn');
      box.append(el('div', {}, el('span', {class:'pill ' + pill, text:r.state}), ' ', r.topic,
        r.aggregate ? ` · 聚合 ${r.aggregate}` : '', r.error ? ` · ${r.error}` : '',
        r.branches ? el('ul', {}, ...Object.entries(r.branches).map(([ch,b]) => el('li', {text:`${ch}：${b.state}`}))) : ''));
    }
  } catch (e) { q('#runs').textContent = e.message; }
}

async function loadCards() {
  try {
    const {cards} = await api('/api/cards');
    const box = q('#cards'); box.innerHTML = '';
    if (!cards.length) { box.textContent = '当前没有待批卡片。'; return; }
    for (const c of cards) {
      const decide = async (action, text, confidence) => {
        try {
          const r = await api('/api/cards/decide', {method:'POST', body: JSON.stringify({gate_id:c.gate_id, action, text, confidence})});
          if (r.applied) { alert('已记录：' + r.decision); loadCards(); loadLedger(); loadAudit(); }
          else {
            store.pending = r.receipt_id;
            q('#receiptEcho').textContent = r.echo;
            q('#receipt').classList.remove('hidden');
            if (!r.needs_confirm) { alert('我没完全懂，请把要求再说具体一点'); q('#receipt').classList.add('hidden'); }
          }
        } catch (e) { alert(e.message); }
      };
      const custom = el('textarea', {placeholder:'不按建议走的话，写清你要什么（例：换个角度，别做盘点）'});
      box.append(el('div', {class:'card'},
        el('div', {class:'ask', text: `${c.gate}${c.channel ? ' · ' + c.channel : ''}  ${c.ask}`}),
        el('div', {class:'meta', text:`建议：${c.recommendation}（把握 ${c.confidence}）  ${c.at}`}),
        el('ul', {}, ...(c.reasons||[]).map(x => el('li', {text:x}))),
        el('div', {class:'meta', text:'风险：' + (c.risk||'—')}),
        el('div', {class:'meta', text:c.question||''}),
        el('div', {class:'row', style:'margin-top:8px'},
          el('button', {class:'primary', onclick: () => decide('approve'), text:'同意'}),
          el('button', {onclick: () => decide('reject'), text:'拒绝'})),
        el('div', {class:'row', style:'margin-top:8px'}, custom,
          el('button', {onclick: () => decide('custom', custom.value, 0.75), text:'自定义回复'}))));
    }
  } catch (e) { q('#cards').textContent = e.message; }
}

async function loadLedger() {
  try {
    const {decisions} = await api('/api/ledger');
    const box = q('#ledger'); box.innerHTML = '';
    if (!decisions.length) { box.textContent = '台账还是空的。'; return; }
    box.append(el('ul', {}, ...decisions.slice(-15).reverse().map(d =>
      el('li', {text:`${d.id} [${d.decided_at}] ${d.topic} → ${d.conclusion}`}))));
  } catch (e) { q('#ledger').textContent = e.message; }
}

async function loadAudit() {
  try {
    const {events} = await api('/api/audit?n=40');
    q('#audit').textContent = events.map(e => `${e.at}  ${e.event}  ${JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k]) => !['at','event'].includes(k))))}`).join('\n');
  } catch (e) { q('#audit').textContent = e.message; }
}

q('#runBtn').onclick = async () => {
  const topic = q('#topic').value.trim();
  if (!topic) { alert('先写题目'); return; }
  try {
    await api('/api/run', {method:'POST', body: JSON.stringify({topic, master_form: q('#form').value, with_vault: q('#withVault').checked})});
    q('#topic').value = '';
    setTimeout(() => { loadRuns(); loadCards(); loadAudit(); }, 600);
  } catch (e) { alert(e.message); }
};
q('#confirmYes').onclick = async () => {
  try {
    await api('/api/cards/confirm', {method:'POST', body: JSON.stringify({receipt_id: store.pending, confirmed: true})});
    q('#receipt').classList.add('hidden'); loadCards(); loadLedger(); loadAudit();
  } catch (e) { alert(e.message); }
};
q('#confirmNo').onclick = async () => {
  try {
    await api('/api/cards/confirm', {method:'POST', body: JSON.stringify({receipt_id: store.pending, confirmed: false})});
    q('#receipt').classList.add('hidden'); loadAudit();
  } catch (e) { alert(e.message); }
};

loadStatus(); loadConfig(); loadRuns(); loadCards(); loadLedger(); loadAudit();
setInterval(loadRuns, 5000);
</script>
</body>
</html>
"""
