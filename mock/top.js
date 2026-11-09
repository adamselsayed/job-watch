// Mockup only: builds a dashboard-style top section from the live data and swaps it in for the plain tiles.
window.__buildTop = async (variant) => {
  const [d, m] = await Promise.all([fetch('data/jobs.json').then(r => r.json()), fetch('data/marks.json').then(r => r.json()).catch(() => ({ marks: {}, groups: {} }))]);
  const marks = m.marks || {}, g = m.groups || {};
  const grp = n => (g.primary || []).includes(n) ? 'primary' : (g.tertiary || []).includes(n) ? 'tertiary' : (g.out || []).includes(n) ? 'out' : 'secondary';
  const open = d.jobs.filter(j => j.status === 'open');
  const status = j => (marks[j.key] || {}).status;
  const untriaged = open.filter(j => !status(j));
  const applied = open.filter(j => status(j) === 'applied');
  const archived = open.filter(j => status(j) === 'archived');
  const when = j => new Date(j.published_at || j.first_seen);
  const day = 864e5, today = new Date(); today.setHours(0, 0, 0, 0);
  const days = [...Array(14)].map((_, i) => { const s = +today - (13 - i) * day; return { s, n: open.filter(j => { const t = +when(j); return t >= s && t < s + day; }).length }; });
  const last7 = days.slice(7).reduce((a, x) => a + x.n, 0), prev7 = days.slice(0, 7).reduce((a, x) => a + x.n, 0);
  const newN = +(document.querySelector('#tNew')?.textContent) || 0;
  const fresh = [...untriaged].sort((a, b) => when(b) - when(a)).slice(0, Math.max(newN, 4));
  const byG = ['primary', 'secondary', 'tertiary', 'out'].map(k => ({ k, n: untriaged.filter(j => grp(j.company) === k).length }));
  const GL = { primary: 'Primary', secondary: 'Secondary', tertiary: 'Tertiary', out: 'Out of network' };
  const remote = untriaged.filter(j => j.remote === 'remote').length, hybrid = untriaged.filter(j => j.remote === 'hybrid').length;
  const pay = untriaged.map(j => j.salary).filter(s => s && s.max > 1000).map(s => (s.min + s.max) / 2).sort((a, b) => a - b);
  const medPay = pay.length ? pay[Math.floor(pay.length / 2)] : 0;
  const top = Object.entries(untriaged.reduce((a, j) => (a[j.company] = (a[j.company] || 0) + 1, a), {})).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const k = v => '$' + Math.round(v / 1000) + 'K';
  const fmtD = t => new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const max = Math.max(1, ...days.map(x => x.n));
  const bars = (h = 64) => `<div class="spark" style="height:${h}px">${days.map((x, i) => `<i class="${i >= 7 ? 'wk' : ''}" style="height:${Math.max(3, x.n / max * 100)}%" title="${fmtD(x.s)}: ${x.n}"></i>`).join('')}</div>
    <div class="axis"><span>${fmtD(days[0].s)}</span><span>Today</span></div>`;
  const stack = (parts, cls = '') => `<div class="stack ${cls}">${parts.filter(p => p.n).map(p => `<i class="g-${p.k}" style="flex:${p.n}" title="${p.label || GL[p.k]}: ${p.n}"></i>`).join('')}</div>`;
  const legend = parts => `<ul class="legend">${parts.map(p => `<li><b class="g-${p.k}"></b>${p.label || GL[p.k]}<span>${p.n}</span></li>`).join('')}</ul>`;
  const trend = last7 - prev7, trendTxt = trend === 0 ? 'same as the week before' : `${Math.abs(trend)} ${trend > 0 ? 'more' : 'fewer'} than the week before`;
  const roleRow = j => `<li><span class="co">${esc(j.company)}</span><a>${esc(j.title)}</a><span class="pay">${j.salary && j.salary.max > 1000 ? k(j.salary.min) + '–' + k(j.salary.max) : ''}</span></li>`;

  let html = '';
  if (variant === 'a') html = `
    <section class="dash a">
      <article class="card hot"><h3>New since your last visit</h3><div class="big">${newN}</div>
        <ul class="roles">${fresh.slice(0, 3).map(roleRow).join('')}</ul></article>
      <article class="card"><h3>Posting activity</h3><div class="big">${last7}<small>posted in the last 7 days</small></div>${bars(56)}<p class="foot">${trendTxt}</p></article>
      <article class="card"><h3>Your pipeline</h3><div class="big">${untriaged.length}<small>open, not yet triaged</small></div>
        ${stack([{ k: 'un', n: untriaged.length, label: 'Not triaged' }, { k: 'ap', n: applied.length, label: 'Applied' }, { k: 'ar', n: archived.length, label: 'Archived' }])}
        ${legend([{ k: 'ap', n: applied.length, label: 'Applied, still open' }, { k: 'ar', n: archived.length, label: 'Archived, still open' }])}</article>
      <article class="card"><h3>Where the roles are</h3><div class="big">${d.companies.length}<small>companies watched</small></div>${stack(byG)}${legend(byG)}</article>
    </section>`;
  if (variant === 'b') html = `
    <section class="dash b">
      <article class="card brief"><p class="date">${new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}</p>
        <h2>${newN ? `${newN} new role${newN > 1 ? 's' : ''} since your last visit` : 'Nothing new since your last visit'}</h2>
        <ul class="roles">${fresh.slice(0, 4).map(roleRow).join('')}</ul></article>
      <div class="side">
        <article class="card"><h3>Posted per day, last two weeks</h3>${bars(78)}<p class="foot"><b>${last7}</b> this week · ${trendTxt}</p></article>
        <div class="pair">
          <article class="card mini"><div class="big">${untriaged.length}</div><p>Not yet triaged</p></article>
          <article class="card mini"><div class="big">${applied.length}</div><p>Applied, still open</p></article>
          <article class="card mini"><div class="big">${medPay ? k(medPay) : '—'}</div><p>Typical pay midpoint</p></article>
        </div>
      </div>
    </section>`;
  if (variant === 'c') html = `
    <section class="dash c card">
      <div class="flow">
        <div class="step"><div class="big">${d.companies.length}</div><p>Companies watched</p></div>
        <div class="arrow"></div>
        <div class="step"><div class="big">${open.length}</div><p>Open product roles</p></div>
        <div class="arrow"></div>
        <div class="step wide"><div class="big">${untriaged.length}</div><p>Waiting for you to triage</p>${stack(byG, 'tall')}${legend(byG)}</div>
        <div class="arrow"></div>
        <div class="step"><div class="big accent">${applied.length}</div><p>Applied, still open</p></div>
      </div>
      <div class="facts">
        <div><h3>New since last visit</h3><div class="big ochre">${newN}</div></div>
        <div><h3>Posted this week</h3><div class="big">${last7}</div>${bars(34)}</div>
        <div><h3>Remote or hybrid</h3><div class="big">${Math.round((remote + hybrid) / Math.max(1, untriaged.length) * 100)}%</div><p class="foot">${remote} remote · ${hybrid} hybrid</p></div>
        <div><h3>Most open roles</h3><ol class="tops">${top.map(([n, c]) => `<li><span>${esc(n)}</span><b>${c}</b></li>`).join('')}</ol></div>
      </div>
    </section>`;
  const tiles = document.querySelector('.tiles');
  tiles.insertAdjacentHTML('beforebegin', html); tiles.style.display = 'none';
};
