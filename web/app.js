/* 个人 Agent 活动监工台 前端
   2 秒轮询；任务卡 DOM 常驻（发话输入框在刷新间保留），仅更新变化字段 */

const REFRESH_MS = 2000;
const STATUS_TEXT = { working: '开工', waiting: '等回话', idle: '摸鱼', offline: '无活动', error: '异常' };
const STATUS_ORDER = { waiting: 0, working: 1, error: 2, idle: 3, offline: 4 };

let currentFilter = 'all';
let agentHash = {};          // agent 头部指纹
let taskNodes = {};          // {agent: {taskId: node}}
let taskHash = {};           // {agent: {taskId: hash}}
let toastTimer = null;

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function toast(msg, ok = true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (ok ? '' : ' err');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 3200);
}

async function api(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

/* ── Tab 切换 ─────────────────────────────── */
const TAB_IDS = ['overview', 'dashboard', 'skills', 'journal'];
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    TAB_IDS.forEach(t => {
      const el = document.getElementById('tab-' + t);
      if (el) el.style.display = (btn.dataset.tab === t) ? '' : 'none';
    });
    if (btn.dataset.tab === 'journal') loadJournal();
  });
});

/* ── 统计卡筛选 ─────────────────────────────── */
document.querySelectorAll('.stat').forEach(el => {
  el.addEventListener('click', () => {
    const f = el.dataset.filter;
    currentFilter = (currentFilter === f) ? 'all' : f;
    document.querySelectorAll('.stat').forEach(s =>
      s.classList.toggle('selected', s.dataset.filter === currentFilter && currentFilter !== 'all'));
    document.getElementById('filterHint').textContent =
      currentFilter === 'all' ? '' : `已筛选: ${STATUS_TEXT[currentFilter] || ''}（再点一次取消）`;
    applyFilter();
  });
});

function matchFilter(status) {
  if (currentFilter === 'all') return true;
  if (currentFilter === 'idle') return status === 'idle' || status === 'offline';
  return status === currentFilter;
}

function applyFilter() {
  document.querySelectorAll('.agent-card').forEach(card => {
    card.classList.toggle('filtered-out', !matchFilter(card.dataset.status));
  });
}

/* ── Agent 大卡片 ─────────────────────────────── */

function ensureAgentCard(agent) {
  let card = document.getElementById('card-' + agent.name);
  if (card) return card;
  card = document.createElement('div');
  card.className = 'agent-card';
  card.id = 'card-' + agent.name;
  card.innerHTML = `
    <div class="agent-header">
      <span class="agent-name">${esc(agent.name)}</span>
      <span class="agent-alias"></span>
      <span class="alias-edit" title="起花名">✎ 改花名</span>
      <span class="agent-status"></span>
    </div>
    <div class="agent-meta"></div>
    <div class="task-grid"></div>`;
  document.getElementById('agentsStack').appendChild(card);

  card.querySelector('.alias-edit').addEventListener('click', async () => {
    const cur = card.querySelector('.agent-alias').textContent;
    const alias = prompt(`给 ${agent.name} 起个花名（留空清除）`, cur);
    if (alias === null) return;
    await api('/api/alias', { agent: agent.name, alias });
    toast(alias ? `花名已保存: ${alias}` : '花名已清除');
  });
  taskNodes[agent.name] = {};
  taskHash[agent.name] = {};
  return card;
}

/* 任务(项目)卡片: DOM 只建一次，动态字段单独更新，textarea 永不重建 */
function ensureTaskCard(agent, task, grid) {
  let node = taskNodes[agent.name][task.id];
  if (node) return node;
  node = document.createElement('div');
  node.className = 'task-card';
  node.dataset.task = task.id;
  node.innerHTML = `
    <div class="task-top">
      <span class="task-chip"></span>
      <span class="task-project"></span>
      <span class="task-open" title="跳转到会话">跳转 ↗</span>
    </div>
    <div class="task-title" title="点击跳转到会话"></div>
    <div class="task-times"></div>
    <div class="task-lastmsg-label">最近动静</div>
    <div class="task-lastmsg"><div class="lastmsg-text"></div></div>
    <div class="task-send">
      <textarea rows="1" placeholder="给它发话…"></textarea>
      <button>发送</button>
    </div>`;
  grid.appendChild(node);

  const jump = async () => {
    const r = await api('/api/open', { agent: agent.name, task_id: task.id });
    toast(r.detail || '', r.ok);
  };
  node.querySelector('.task-open').addEventListener('click', jump);
  node.querySelector('.task-title').addEventListener('click', jump);

  const ta = node.querySelector('textarea');
  const btn = node.querySelector('button');
  const doSend = async () => {
    const msg = ta.value;
    btn.disabled = true; btn.textContent = '…';
    try {
      const r = await api('/api/send', { agent: agent.name, task_id: task.id, message: msg });
      toast(r.detail || (r.ok ? '已发送' : '发送失败'), r.ok);
      if (r.ok) ta.value = '';
    } catch (e) {
      toast('发送失败: ' + e, false);
    }
    btn.disabled = false; btn.textContent = '发送';
  };
  btn.addEventListener('click', doSend);
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); }
  });

  taskNodes[agent.name][task.id] = node;
  return node;
}

function updateTaskCard(agent, task, grid, order) {
  const node = ensureTaskCard(agent, task, grid);
  node.style.order = order;
  const hash = JSON.stringify([task.status, task.title, task.project, task.started,
    task.last_active, task.duration_min, task.last_msg, task.last_role]);
  if (taskHash[agent.name][task.id] === hash) return;
  taskHash[agent.name][task.id] = hash;

  node.className = 'task-card t-' + task.status;
  const chip = node.querySelector('.task-chip');
  chip.textContent = STATUS_TEXT[task.status] || task.status;
  chip.className = 'task-chip chip-' + (task.status === 'working' ? 'working' : task.status === 'waiting' ? 'waiting' : 'idle');
  const proj = node.querySelector('.task-project');
  proj.textContent = task.project || '';
  proj.style.display = task.project ? '' : 'none';
  node.querySelector('.task-title').textContent = task.title;
  node.querySelector('.task-times').innerHTML =
    `<span>开始 <b>${esc(task.started || '--')}</b></span>` +
    `<span>最后活跃 <b>${esc(task.last_active || '--')}</b></span>` +
    (task.duration_min > 0 ? `<span>已持续 <b>${task.duration_min}min</b></span>` : '');
  node.querySelector('.lastmsg-text').innerHTML = task.last_msg
    ? `<span class="who">${task.last_role === 'user' ? '我' : 'Agent'}</span>${esc(task.last_msg)}`
    : '<span class="who">·</span>暂无消息记录';
}

function renderAgent(agent) {
  const card = ensureAgentCard(agent);
  card.dataset.status = agent.status;
  card.style.order = STATUS_ORDER[agent.status] ?? 9;
  card.classList.toggle('filtered-out', !matchFilter(agent.status));
  card.className = card.className.replace(/ st-\w+/g, '') + ' st-' + agent.status;

  // 头部
  const hHash = JSON.stringify([agent.status, agent.alias, agent.today_count, agent.working_count, agent.waiting_count, agent.error]);
  if (agentHash[agent.name] !== hHash) {
    agentHash[agent.name] = hHash;
    card.querySelector('.agent-alias').textContent = agent.alias || '';
    const badge = card.querySelector('.agent-status');
    badge.textContent = STATUS_TEXT[agent.status] || agent.status;
    badge.className = 'agent-status badge-' + agent.status;
    card.querySelector('.agent-meta').innerHTML = agent.error
      ? `<span style="color:var(--red)">${esc(agent.error).slice(0, 80)}</span>`
      : `今日 ${agent.today_count} 个任务 ·
         <span class="m-working">${agent.working_count} 开工</span> ·
         <span class="m-waiting">${agent.waiting_count} 等回话</span>`;
  }

  // 任务卡: 更新/新建 + 清理过期
  const grid = card.querySelector('.task-grid');
  const alive = new Set();
  (agent.recent_tasks || []).forEach((t, i) => {
    alive.add(t.id);
    updateTaskCard(agent, t, grid, i);
  });
  Object.keys(taskNodes[agent.name]).forEach(id => {
    if (!alive.has(id)) {
      taskNodes[agent.name][id].remove();
      delete taskNodes[agent.name][id];
      delete taskHash[agent.name][id];
    }
  });
  let empty = grid.querySelector('.empty');
  if (!alive.size && !empty) {
    empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '今日暂无活动';
    grid.appendChild(empty);
  } else if (alive.size && empty) {
    empty.remove();
  }
}

/* 让 agents-stack 支持 style.order */
document.getElementById('agentsStack').style.display = 'flex';

/* ── 时间线 ─────────────────────────────────── */
let lastTimelineHash = '';

function renderTimeline(agents) {
  const all = [];
  agents.forEach(a => (a.recent_tasks || []).forEach(t => all.push({ ...t, source: a.name })));
  all.sort((x, y) => y.last_active_ts - x.last_active_ts);
  const top = all.slice(0, 15);

  const hash = JSON.stringify(top.map(t => [t.source, t.id, t.last_active, t.status]));
  if (hash === lastTimelineHash) return;
  lastTimelineHash = hash;

  const el = document.getElementById('timeline');
  el.innerHTML = top.length
    ? top.map(t => `
      <div class="timeline-item" data-agent="${esc(t.source)}" data-task="${esc(t.id)}" title="点击跳转到会话">
        <div class="timeline-time">${t.last_active ? t.last_active.slice(0, 5) : ''}</div>
        <div class="timeline-content">
          <span class="timeline-source">${esc(t.source)}</span>
          ${t.project ? `<span class="timeline-project">${esc(t.project)}</span>` : ''}
          ${t.status !== 'idle' ? `<span class="dot ${t.status}"></span>` : ''}
          ${esc(t.title)}
        </div>
      </div>`).join('')
    : '<div class="empty">今日暂无活动记录</div>';

  el.querySelectorAll('.timeline-item').forEach(item => {
    item.addEventListener('click', async () => {
      const r = await api('/api/open', { agent: item.dataset.agent, task_id: item.dataset.task });
      toast(r.detail || '', r.ok);
    });
  });
}

/* ── Skill 统计 ─────────────────────────────── */
let skillsData = [];
let skillQuery = '';
let lastSkillsHash = '';

const skillSearchInput = document.getElementById('skillSearch');
const skillSearchClear = document.getElementById('skillSearchClear');
skillSearchInput.addEventListener('input', () => {
  skillQuery = skillSearchInput.value.trim().toLowerCase();
  skillSearchClear.style.display = skillQuery ? 'block' : 'none';
  renderSkillsUI();
});
skillSearchClear.addEventListener('click', () => {
  skillSearchInput.value = '';
  skillQuery = '';
  skillSearchClear.style.display = 'none';
  renderSkillsUI();
  skillSearchInput.focus();
});

function renderSkills(skills) {
  const hash = JSON.stringify(skills);
  if (hash === lastSkillsHash) return;
  lastSkillsHash = hash;
  skillsData = skills || [];
  renderSkillsUI();
}

function filteredSkills() {
  if (!skillQuery) return skillsData;
  return skillsData.filter(s => s.skill.toLowerCase().includes(skillQuery));
}

/* 横向柱状图: 使用次数排行 */
function renderSkillBars(skills) {
  const el = document.getElementById('skillBars');
  const top = skills.slice(0, 10);
  if (!top.length) {
    el.innerHTML = '<div class="empty">无匹配数据</div>';
    return;
  }
  const max = Math.max(...top.map(s => s.count));
  el.innerHTML = top.map(s => `
    <div class="bar-row">
      <div class="bar-name" title="${esc(s.skill)}">${esc(s.skill)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, s.count / max * 100)}%"></div></div>
      <div class="bar-count">${s.count}</div>
    </div>`).join('');
}

/* SVG 面积趋势图通用构建: dayMap = {YYYY-MM-DD: count} */
function trendSVG(dayMap) {
  const dates = Object.keys(dayMap).sort();
  if (!dates.length) return '';
  // 补齐首尾之间的空缺日期
  const series = [];
  const cur = new Date(dates[0] + 'T00:00:00');
  const end = new Date(dates[dates.length - 1] + 'T00:00:00');
  while (cur <= end && series.length < 120) {
    const key = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}-${String(cur.getDate()).padStart(2, '0')}`;
    series.push({ date: key, count: dayMap[key] || 0 });
    cur.setDate(cur.getDate() + 1);
  }
  const W = 460, H = 170, padL = 30, padR = 14, padT = 12, padB = 24;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const maxY = Math.max(...series.map(p => p.count), 1);
  const stepX = series.length > 1 ? innerW / (series.length - 1) : 0;
  const px = i => padL + (series.length > 1 ? i * stepX : innerW / 2);
  const py = v => padT + innerH - (v / maxY) * innerH;
  const pts = series.map((p, i) => `${px(i).toFixed(1)},${py(p.count).toFixed(1)}`);
  const area = `M${px(0).toFixed(1)},${(padT + innerH).toFixed(1)} L` + pts.join(' L') +
    ` L${px(series.length - 1).toFixed(1)},${(padT + innerH).toFixed(1)} Z`;
  const labelStep = Math.max(1, Math.ceil(series.length / 6));
  const labels = series.map((p, i) => {
    const isLast = i === series.length - 1;
    const isTick = i % labelStep === 0 && (series.length - 1 - i) >= labelStep * 0.6; // 避免与末尾标签重叠
    return (isTick || isLast)
      ? `<text class="trend-axis" x="${px(i).toFixed(1)}" y="${H - 6}" text-anchor="middle">${p.date.slice(5)}</text>` : '';
  }).join('');
  const grid = [0.5, 1].map(r =>
    `<line class="trend-grid" x1="${padL}" y1="${py(maxY * r).toFixed(1)}" x2="${W - padR}" y2="${py(maxY * r).toFixed(1)}"/>` +
    `<text class="trend-axis" x="${padL - 6}" y="${(py(maxY * r) + 3).toFixed(1)}" text-anchor="end">${Math.round(maxY * r)}</text>`).join('');
  const dots = series.map((p, i) => p.count > 0
    ? `<circle class="trend-dot" cx="${px(i).toFixed(1)}" cy="${py(p.count).toFixed(1)}" r="3"><title>${p.date} · ${p.count} 次</title></circle>` : '').join('');
  return `
    <svg viewBox="0 0 ${W} ${H}">
      ${grid}
      <path class="trend-area" d="${area}"/>
      <polyline class="trend-line" points="${pts.join(' ')}"/>
      ${dots}
      ${labels}
    </svg>`;
}

/* SVG 面积趋势图: 每日使用总量（随搜索过滤） */
function renderSkillTrend(skills) {
  const el = document.getElementById('skillTrend');
  const dayMap = {};
  skills.forEach(s => Object.entries(s.daily || {}).forEach(([d, n]) => {
    dayMap[d] = (dayMap[d] || 0) + n;
  }));
  el.innerHTML = trendSVG(dayMap) || '<div class="empty">无匹配数据</div>';
}

function renderSkillsUI() {
  const skills = filteredSkills();
  renderSkillBars(skills);
  renderSkillTrend(skills);

  const el = document.getElementById('skillsPanel');
  if (!skills.length) {
    el.innerHTML = `<div class="empty">${skillQuery ? `未找到包含“${esc(skillQuery)}”的 Skill` : '暂无 Skill 使用记录'}</div>`;
    return;
  }
  el.innerHTML = `
    <table class="skills-table">
      <thead><tr>
        <th>Skill</th><th>使用次数</th><th>使用项目</th><th>来源 Agent</th><th>活跃天数</th><th>最近使用</th>
      </tr></thead>
      <tbody>
        ${skills.map(s => `
        <tr>
          <td class="skill-name">${esc(s.skill)}</td>
          <td><span class="skill-count">${s.count}</span></td>
          <td>${(s.projects || []).map(p => `<span class="skill-tag">${esc(p)}</span>`).join('') || '<span style="color:var(--text-dim)">—</span>'}</td>
          <td>${(s.agents || []).map(a => `<span class="skill-agent">${esc(a)}</span>`).join('')}</td>
          <td style="color:var(--text-dim)">${Object.keys(s.daily || {}).length} 天</td>
          <td style="color:var(--text-dim)">${esc(s.last_used || '')}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

/* ── 主循环 ─────────────────────────────────── */
/* ── 工作总览 ───────────────────── */
let lastOverviewHash = '';

function fmtDur(min) {
  if (min >= 60) return `${Math.floor(min / 60)}<span class="unit">h</span>${min % 60 ? (min % 60) + '<span class="unit">m</span>' : ''}`;
  return `${min}<span class="unit">min</span>`;
}

function renderOverview(overview) {
  const agents = (overview && overview.agents) || [];
  const hash = JSON.stringify(agents);
  if (hash === lastOverviewHash) return;
  lastOverviewHash = hash;

  // 指标卡
  const todayCount = agents.reduce((s, a) => s + a.today_count, 0);
  const todayMin = agents.reduce((s, a) => s + a.today_min, 0);
  const sessions = agents.reduce((s, a) => s + a.sessions, 0);
  const totalMin = agents.reduce((s, a) => s + a.duration_min, 0);
  document.getElementById('ovToday').textContent = todayCount;
  document.getElementById('ovTodayDur').innerHTML = fmtDur(todayMin);
  document.getElementById('ovSessions').textContent = sessions;
  document.getElementById('ovDur').innerHTML = fmtDur(totalMin);

  // 各 Agent 累计会话柱状图
  const barsEl = document.getElementById('ovBars');
  if (agents.length) {
    const max = Math.max(...agents.map(a => a.sessions), 1);
    barsEl.innerHTML = agents.map(a => `
      <div class="bar-row">
        <div class="bar-name" title="${esc(a.name)}">${esc(a.name)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, a.sessions / max * 100)}%"></div></div>
        <div class="bar-count">${a.sessions}</div>
      </div>`).join('');
  } else {
    barsEl.innerHTML = '<div class="empty">暂无数据</div>';
  }

  // 每日活动趋势（全部 Agent 合并）
  const dayMap = {};
  agents.forEach(a => Object.entries(a.daily || {}).forEach(([d, n]) => {
    dayMap[d] = (dayMap[d] || 0) + n;
  }));
  document.getElementById('ovTrend').innerHTML = trendSVG(dayMap) || '<div class="empty">暂无数据</div>';

  // 指标表
  const tbl = document.getElementById('ovTable');
  tbl.innerHTML = agents.length ? `
    <table class="skills-table">
      <thead><tr>
        <th>Agent</th><th>今日任务</th><th>今日投入</th><th>累计会话</th><th>累计时长</th><th>活跃天数</th><th>日均会话</th>
      </tr></thead>
      <tbody>
        ${agents.map(a => `
        <tr>
          <td class="skill-name">${esc(a.name)}</td>
          <td><span class="skill-count">${a.today_count}</span></td>
          <td style="color:var(--text-dim)">${a.today_min} min</td>
          <td>${a.sessions}</td>
          <td style="color:var(--text-dim)">${Math.floor(a.duration_min / 60)}h ${a.duration_min % 60}m</td>
          <td>${a.active_days} 天</td>
          <td style="color:var(--text-dim)">${a.active_days ? (a.sessions / a.active_days).toFixed(1) : '0'}</td>
        </tr>`).join('')}
      </tbody>
    </table>` : '<div class="empty">暂无数据</div>';
}

/* ── 每日总结 ───────────────────── */
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function fmtJournalDate(d) {
  const dt = new Date(d + 'T00:00:00');
  return isNaN(dt) ? d : `${d} · ${WEEKDAYS[dt.getDay()]}`;
}

async function loadJournal() {
  try {
    const res = await fetch('/api/journal');
    const data = await res.json();
    renderJournal(data);
  } catch (e) {
    toast('总结加载失败: ' + e, false);
  }
}

function renderJournal(data) {
  const today = data.today || '';
  const entries = data.entries || [];
  document.getElementById('journalToday').textContent = `今日 · ${fmtJournalDate(today)}`;

  const todayEntry = entries.find(en => en.date === today);
  document.getElementById('journalHint').textContent =
    todayEntry ? `今日已保存（${todayEntry.updated_at}），再次保存会覆盖` : '今日尚未保存';

  const list = document.getElementById('journalList');
  list.innerHTML = entries.length
    ? entries.map(en => `
      <div class="journal-entry">
        <div class="journal-entry-head">
          <span class="journal-entry-date">${esc(fmtJournalDate(en.date))}</span>
          ${en.date === today ? '<span class="journal-entry-badge">今天</span><span class="journal-entry-edit" title="载入输入框继续修改">✎ 编辑</span>' : ''}
          <span class="journal-entry-time">更新于 ${esc(en.updated_at)}</span>
        </div>
        <div class="journal-entry-text">${esc(en.text)}</div>
      </div>`).join('')
    : '<div class="empty">暂无历史总结，写下第一篇吧</div>';

  // 今日条目的“编辑”：把已保存内容载回输入框继续修改
  const editBtn = list.querySelector('.journal-entry-edit');
  if (editBtn && todayEntry) {
    editBtn.addEventListener('click', () => {
      const ta = document.getElementById('journalText');
      ta.value = todayEntry.text;
      ta.focus();
      toast('已载入今日内容，修改后重新保存即可');
    });
  }
}

document.getElementById('journalSave').addEventListener('click', async () => {
  const btn = document.getElementById('journalSave');
  const ta = document.getElementById('journalText');
  const text = ta.value;
  if (!text.trim()) {
    toast('内容为空，未保存；如需修改今日总结，点历史条目的“编辑”', false);
    return;
  }
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const res = await fetch('/api/journal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    ta.value = '';   // 保存成功后清空，恢复占位符提示
    renderJournal({ today: data.date, entries: data.entries });
    toast('今日总结已保存');
  } catch (e) {
    toast('保存失败: ' + e, false);
  }
  btn.disabled = false; btn.textContent = '保存今日总结';
});

/* ── 主循环 ── */
async function refresh() {
  try {
    const res = await fetch('/api/state');
    const data = await res.json();
    const stats = data.today_stats || {};
    document.getElementById('updateTime').textContent = '更新于 ' + (data.updated_at || '').slice(11, 19);
    document.getElementById('statWaiting').textContent = stats.waiting_agents || 0;
    document.getElementById('statWorking').textContent = stats.working_agents || 0;
    document.getElementById('statIdle').textContent = stats.idle_agents || 0;
    document.getElementById('statTotal').textContent = stats.total_tasks || 0;
    (data.agents || []).forEach(renderAgent);
    renderTimeline(data.agents || []);
    renderSkills(data.skills || []);
    renderOverview(data.overview || {});
  } catch (e) {
    document.getElementById('updateTime').textContent = '连接断开，重试中…';
  }
}

refresh();
loadJournal();
setInterval(refresh, REFRESH_MS);
