/*
 * Markets tab rendering: instrument cards with sparklines, grouped by
 * category, with a click-through detail chart.
 *
 * Ported from an earlier single-file dashboard. That version
 * carried two near-identical engines -- one for Global Markets, one for the
 * BTC Tracker -- because it had two tabs with separate endpoints and
 * separate DOM containers. There are four tabs here, so a second copy would
 * have become a fourth; instead this is one engine parameterised by
 * MKT_TAB, set by the template.
 *
 * Group ordering comes from the server (the order instruments are defined
 * in the registry), so the hardcoded GROUP_ORDER array the original needed
 * is gone too.
 */

const mktState = { period: MKT_DEFAULT_PERIOD, data: null, selectedId: null };

// Plotly dark defaults, matching the dashboard palette.
const _dkAxis = {
  gridcolor: '#2a3650', linecolor: '#2a3650', zerolinecolor: '#2a3650',
  tickfont: { size: 11, color: '#8896af' },
};
function darkLayout(extra) {
  return Object.assign({
    paper_bgcolor: '#192030',
    plot_bgcolor: '#131b28',
    font: { color: '#e2e8f0', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
    hovermode: 'closest',
  }, extra);
}

async function api(url, timeout = 60000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url, { signal: ctrl.signal });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    console.error('api', url, e);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function loadMarkets(period) {
  // The period selector is a row of links in the page chrome now (it applies
  // to every tab), so this no longer toggles button state -- the server
  // renders which one is active from the query string.
  mktState.period = period;
  mktState.data = null;
  const groupsEl = document.getElementById('mkt-groups');
  groupsEl.innerHTML = '<div class="loading">Loading…</div>';

  const data = await api('/api/markets/' + MKT_TAB + '?period=' + period);
  if (!data || !data.instruments) {
    groupsEl.innerHTML = '<div class="loading">Failed to load market data — check the Status page.</div>';
    return;
  }
  mktState.data = data;

  const updEl = document.getElementById('mkt-updated');
  if (updEl) {
    // Latest collection time across the tab, not the render time -- what the
    // reader wants to know is how fresh the data is, not when the page drew.
    const stamps = data.instruments.map(i => i.fetched_at).filter(Boolean).sort();
    if (stamps.length) {
      const t = new Date(stamps[stamps.length - 1]);
      updEl.textContent = 'Collected ' + t.toLocaleString('en-GB',
        { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    }
  }
  renderMktGroups(data.instruments, data.group_meta || {});
}

function renderMktGroups(instruments, groupMeta) {
  const groupsEl = document.getElementById('mkt-groups');
  groupsEl.innerHTML = '';
  const byGroup = {};
  const order = [];
  for (const inst of instruments) {
    if (!byGroup[inst.group]) { byGroup[inst.group] = []; order.push(inst.group); }
    byGroup[inst.group].push(inst);
  }
  for (const grp of order) {
    const section = buildMktGroupSection(grp, byGroup[grp], groupMeta);
    if (section) groupsEl.appendChild(section);
  }
}

function buildMktGroupSection(grp, insts, groupMeta) {
  if (!insts || !insts.length) return null;
  // Every instrument renders, including ones with no data -- they show as
  // dimmed placeholders rather than vanishing, so the grid doesn't reshuffle
  // when you change period and a short series drops out.
  const meta = groupMeta[grp] || {};
  const wrapper = document.createElement('div');
  wrapper.className = 'mkt-group';

  const title = document.createElement('div');
  title.className = 'mkt-group-title';
  title.textContent = grp;
  wrapper.appendChild(title);

  if (meta.desc) {
    const desc = document.createElement('div');
    desc.className = 'mkt-group-desc';
    desc.textContent = meta.desc;
    wrapper.appendChild(desc);
  }

  const grid = document.createElement('div');
  grid.className = 'mkt-grid';
  for (const inst of insts) grid.appendChild(buildMktCard(inst));
  wrapper.appendChild(grid);
  return wrapper;
}

function formatMktValue(v, dp, unit) {
  if (v == null) return { main: '—', sub: '' };
  const n = Number(v).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  if (!unit) return { main: n, sub: '' };
  if (unit === '$') return { main: '$' + n, sub: '' };
  if (unit === '$B') return { main: '$' + n + 'B', sub: '' };
  if (unit === '%') return { main: n + '%', sub: '' };
  if (unit === '% GDP') return { main: n + '%', sub: 'of GDP' };
  if (unit === 'pts') return { main: n, sub: 'pts' };
  if (unit.startsWith('$/')) return { main: '$' + n, sub: 'per ' + unit.slice(2) };
  return { main: n, sub: unit };
}

// Shared by card and detail view so a value can't be coloured one way in the
// grid and another way when opened.
function changeClass(inst, chgPct) {
  if (inst.no_color || chgPct == null) return 'flat';
  const rising = chgPct > 0.05, falling = chgPct < -0.05;
  // invert_color: bond yields, CPI, debt, energy -- rising is the bad
  // direction, so it reads red rather than green.
  if (inst.invert_color) return rising ? 'neg' : falling ? 'pos' : 'flat';
  return rising ? 'pos' : falling ? 'neg' : 'flat';
}

function buildMktCard(inst) {
  const hasData = inst.current != null || (inst.history && inst.history.some(h => h.close != null));
  const card = document.createElement('div');
  card.className = 'mkt-card' + (inst.id === mktState.selectedId ? ' selected' : '') +
                   (hasData ? '' : ' mkt-card--empty');
  card.dataset.id = inst.id;

  const { main: valStr, sub: unitSub } = formatMktValue(inst.current, inst.dp, inst.unit);
  const chgPct = inst.period_change_pct;          // first -> last over the period
  const chgClass = changeClass(inst, chgPct);
  const suffix = inst.bond_yield ? ' pp' : '%';   // a yield's change is in points, not % of a %
  const chgStr = chgPct != null
    ? (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + suffix
    : (inst.error || '—');
  const periodLabel = inst.first_ts && inst.last_ts
    ? inst.first_ts.slice(0, 7) + ' → ' + inst.last_ts.slice(0, 7) : '';
  const sparkSVG = (inst.history && inst.history.length > 1)
    ? '<svg class="mkt-spark" viewBox="0 0 100 36" preserveAspectRatio="none">' +
      makeSpark(inst.history, chgClass) + '</svg>'
    : '';

  card.innerHTML =
    '<div class="mkt-card-label"></div>' +
    '<div class="mkt-card-value"></div>' +
    (unitSub ? '<div class="mkt-card-unit"></div>' : '') +
    '<div class="mkt-card-change ' + chgClass + '"></div>' +
    (periodLabel ? '<div class="mkt-card-period"></div>' : '') +
    sparkSVG;
  // textContent rather than interpolation: instrument labels and error
  // strings come from the registry and upstream providers, and one day an
  // error message will contain a stray angle bracket.
  card.querySelector('.mkt-card-label').textContent = inst.label;
  card.querySelector('.mkt-card-value').textContent = valStr;
  if (unitSub) card.querySelector('.mkt-card-unit').textContent = unitSub;
  card.querySelector('.mkt-card-change').textContent = chgStr;
  if (periodLabel) card.querySelector('.mkt-card-period').textContent = periodLabel;

  card.addEventListener('click', () => showMktDetail(inst.id));
  return card;
}

function makeSpark(history, chgClass) {
  if (!history || !history.length) return '';
  // x from timestamps, not index: a null sentinel at the period start then
  // produces a proportionally correct gap instead of a one-pixel offset.
  const tStart = new Date(history[0].ts).getTime();
  const tEnd = new Date(history[history.length - 1].ts).getTime();
  const tRange = tEnd - tStart || 1;
  const valid = history.filter(h => h.close != null);
  if (!valid.length) return '';
  const closes = valid.map(h => h.close);
  const min = Math.min(...closes), max = Math.max(...closes);
  const range = max - min || 1;
  const pts = valid.map(h => {
    const x = (new Date(h.ts).getTime() - tStart) / tRange * 100;
    const y = 34 - ((h.close - min) / range) * 30;
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');
  const color = chgClass === 'pos' ? '#22c55e' : chgClass === 'neg' ? '#f87171' : '#e2e8f0';
  return '<polyline points="' + pts + '" fill="none" stroke="' + color +
         '" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>';
}

function computeSMA(values, window) {
  return values.map((_, i) => {
    if (i < window - 1) return null;
    const slice = values.slice(i - window + 1, i + 1);
    if (slice.some(v => v == null)) return null;
    return slice.reduce((a, b) => a + b, 0) / window;
  });
}

function renderMktDetailChart(inst, fromDate, toDate, maWindow) {
  let h = inst.history || [];
  if (fromDate) h = h.filter(p => p.ts >= fromDate);
  if (toDate) h = h.filter(p => p.ts <= toDate);

  const chartEl = document.getElementById('mkt-detail-chart');
  if (!chartEl) return;
  const statRow = document.getElementById('mkt-stat-row');

  if (!h.length) {
    chartEl.textContent = inst.error || 'No data for this range';
    chartEl.style.cssText = 'height:300px;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:13px;';
    if (statRow) statRow.style.display = 'none';
    return;
  }
  chartEl.style.cssText = 'height:300px;';
  if (statRow) statRow.style.display = '';

  const xs = h.map(p => p.ts);
  const ys = h.map(p => p.close);
  // Skip the null sentinels (period-start anchors) when measuring change.
  const first = ys.find(v => v != null);
  const last = [...ys].reverse().find(v => v != null);
  const chgPct = first != null && last != null
    ? (inst.bond_yield ? last - first : (last - first) / first * 100) : null;
  const chgClass = changeClass(inst, chgPct);
  const lineColor = inst.no_color ? '#e2e8f0'
    : chgClass === 'pos' ? '#22c55e' : chgClass === 'neg' ? '#f87171' : '#e2e8f0';

  if (statRow) {
    const statSuffix = inst.bond_yield ? ' pp' : '%';
    statRow.querySelector('#mkt-stat-start').textContent = formatMktValue(first, inst.dp, inst.unit).main;
    statRow.querySelector('#mkt-stat-start-date').textContent = xs[0] || '—';
    statRow.querySelector('#mkt-stat-end').textContent = formatMktValue(last, inst.dp, inst.unit).main;
    statRow.querySelector('#mkt-stat-end-date').textContent = xs[xs.length - 1] || '—';
    const pctEl = statRow.querySelector('#mkt-stat-chg');
    pctEl.textContent = chgPct != null ? (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + statSuffix : '—';
    pctEl.className = 'mkt-stat-val ' + chgClass;
    statRow.querySelector('#mkt-stat-pts').textContent =
      chgPct != null ? formatMktValue(last - first, inst.dp, inst.unit).main : '—';
  }

  const traces = [{
    x: xs, y: ys, type: 'scatter', mode: 'lines', name: inst.label,
    line: { color: lineColor, width: 2 },
    hovertemplate: '<b>%{x}</b><br>' + (inst.unit || '') + ' %{y:,.2f}<extra></extra>',
  }];
  if (maWindow > 0) {
    traces.push({
      x: xs, y: computeSMA(ys, maWindow), type: 'scatter', mode: 'lines',
      name: maWindow + '-period MA',
      line: { color: '#fb923c', width: 1.5, dash: 'dot' },
      hovertemplate: '<b>%{x}</b><br>MA: %{y:,.2f}<extra></extra>',
    });
  }

  // Threshold reference lines (VIX zones, HY spread zones)
  const thresholds = inst.thresholds || [];
  const shapes = thresholds.map(t => ({
    type: 'line', x0: 0, x1: 1, xref: 'paper', y0: t.value, y1: t.value,
    line: { color: t.color, width: 1, dash: 'dot' },
  }));
  const annotations = thresholds.map(t => ({
    x: 1.01, xref: 'paper', y: t.value, yref: 'y', text: t.label,
    showarrow: false, xanchor: 'left', font: { size: 9, color: t.color },
  }));

  Plotly.react('mkt-detail-chart', traces, darkLayout({
    margin: { t: 10, r: thresholds.length ? 72 : 20, b: 40, l: 70 },
    xaxis: { ..._dkAxis, type: 'date', showgrid: true },
    yaxis: { ..._dkAxis, showgrid: true, tickformat: ',.2f' },
    legend: { orientation: 'h', y: -0.15, x: 0, bgcolor: 'rgba(0,0,0,0)', font: { color: '#8896af' } },
    shapes: shapes.length ? shapes : undefined,
    annotations: annotations.length ? annotations : undefined,
  }), { responsive: true, displayModeBar: false });
}

function showMktDetail(id) {
  mktState.selectedId = id;
  document.querySelectorAll('.mkt-card').forEach(c => c.classList.toggle('selected', c.dataset.id === id));
  const existing = document.getElementById('mkt-detail-overlay');
  if (existing) existing.remove();
  if (!mktState.data) return;
  const inst = mktState.data.instruments.find(i => i.id === id);
  if (!inst) return;

  const chgPct = inst.period_change_pct;
  const chgClass = changeClass(inst, chgPct);
  const chgStr = chgPct != null
    ? (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + (inst.bond_yield ? ' pp' : '%') : '—';
  const meta = (mktState.data.group_meta || {})[inst.group] || {};

  const overlay = document.createElement('div');
  overlay.id = 'mkt-detail-overlay';
  overlay.className = 'mkt-detail-overlay';
  overlay.innerHTML =
    '<div class="mkt-detail-panel">' +
      '<div class="mkt-detail-header">' +
        '<div class="mkt-detail-title-col">' +
          '<div class="mkt-detail-title"></div>' +
          '<div class="mkt-detail-meta"></div>' +
          '<div class="mkt-detail-desc"></div>' +
          '<div class="mkt-detail-source"></div>' +
        '</div>' +
        '<div class="mkt-detail-change ' + chgClass + '"></div>' +
        '<button class="mkt-close-btn" onclick="closeMktDetail()" aria-label="Close">&#10005;</button>' +
      '</div>' +
      '<div class="mkt-detail-body">' +
        '<div class="mkt-detail-controls">' +
          '<label for="mkt-from">From</label><input type="date" id="mkt-from">' +
          '<label for="mkt-to">To</label><input type="date" id="mkt-to">' +
          '<label for="mkt-ma">Rolling avg</label>' +
          '<select id="mkt-ma">' +
            '<option value="0">None</option><option value="7">7-period</option>' +
            '<option value="20" selected>20-period</option><option value="50">50-period</option>' +
          '</select>' +
        '</div>' +
        '<div class="mkt-stat-row" id="mkt-stat-row">' +
          '<div class="mkt-stat-item"><div class="mkt-stat-label">Start</div><div class="mkt-stat-val" id="mkt-stat-start">—</div><div style="font-size:10px;color:var(--muted)" id="mkt-stat-start-date"></div></div>' +
          '<div class="mkt-stat-item"><div class="mkt-stat-label">End</div><div class="mkt-stat-val" id="mkt-stat-end">—</div><div style="font-size:10px;color:var(--muted)" id="mkt-stat-end-date"></div></div>' +
          '<div class="mkt-stat-item"><div class="mkt-stat-label">Change</div><div class="mkt-stat-val" id="mkt-stat-chg">—</div></div>' +
          '<div class="mkt-stat-item"><div class="mkt-stat-label">Change (abs)</div><div class="mkt-stat-val" id="mkt-stat-pts">—</div></div>' +
        '</div>' +
        '<div id="mkt-detail-chart" style="height:300px;"></div>' +
      '</div>' +
    '</div>';

  overlay.querySelector('.mkt-detail-title').textContent = inst.label;
  overlay.querySelector('.mkt-detail-meta').textContent =
    formatMktValue(inst.current, inst.dp, inst.unit).main + (inst.unit ? ' ' + inst.unit : '');
  overlay.querySelector('.mkt-detail-change').textContent = chgStr;

  const descEl = overlay.querySelector('.mkt-detail-desc');
  descEl.textContent = inst.desc || '';
  if (meta.unit_note) {
    const em = document.createElement('em');
    em.style.cssText = 'display:block;font-size:11px;opacity:.7;margin-top:6px;';
    em.textContent = meta.unit_note;
    descEl.appendChild(em);
  }
  // Provenance in the UI, not just the API: the reader can see which
  // provider a number came from without leaving the page.
  const srcEl = overlay.querySelector('.mkt-detail-source');
  if (inst.source) {
    srcEl.textContent = 'Source: ' + (inst.source_provider || inst.source) +
      ' · series ' + inst.series_id +
      (inst.fetched_at ? ' · collected ' + inst.fetched_at.slice(0, 16).replace('T', ' ') : '');
  }

  overlay.addEventListener('click', e => { if (e.target === overlay) closeMktDetail(); });
  document.body.appendChild(overlay);

  const fromEl = document.getElementById('mkt-from');
  const toEl = document.getElementById('mkt-to');
  fromEl.value = (inst.first_ts || '').slice(0, 10);
  toEl.value = (inst.last_ts || '').slice(0, 10);

  const redraw = () => renderMktDetailChart(
    inst, fromEl.value, toEl.value, parseInt(document.getElementById('mkt-ma').value) || 0);
  fromEl.addEventListener('change', redraw);
  toEl.addEventListener('change', redraw);
  document.getElementById('mkt-ma').addEventListener('change', redraw);
  redraw();
}

function closeMktDetail() {
  const overlay = document.getElementById('mkt-detail-overlay');
  if (overlay) overlay.remove();
  mktState.selectedId = null;
  document.querySelectorAll('.mkt-card').forEach(c => c.classList.remove('selected'));
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeMktDetail();
});

loadMarkets(MKT_DEFAULT_PERIOD);
