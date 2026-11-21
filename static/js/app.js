/* PruneMate UI
 * Loaded as an external script so the page can run with a strict CSP
 * (script-src 'self'). All DOM is built with textContent / createElement;
 * data from Docker or the config is never inserted as HTML.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const CSRF = $('meta[name="csrf-token"]')?.content || '';
  const USE_24H = document.body.dataset.clock !== '12';

  /** Create an element. attrs: class, text, dataset, on*, hidden, or plain attributes. */
  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, String(value));
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function icon(name, extraClass = '') {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', ('icon ' + extraClass).trim());
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(SVG_NS, 'use');
    use.setAttribute('href', '#i-' + name);
    svg.appendChild(use);
    return svg;
  }

  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    return node;
  }

  /** fetch() wrapper: adds CSRF + fetch marker headers, parses JSON, handles expired sessions. */
  async function api(url, { method = 'GET', body, json } = {}) {
    const headers = { 'X-Requested-With': 'fetch', 'Accept': 'application/json' };
    if (method !== 'GET') headers['X-CSRF-Token'] = CSRF;
    let payload = body;
    if (json !== undefined) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(json);
    }
    const res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin' });
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON response */ }
    if (res.status === 401 && data.login) {
      toast('Your session expired. Redirecting to sign in…', 'error');
      setTimeout(() => { window.location.href = data.login; }, 1200);
      throw new Error('Session expired');
    }
    if (!res.ok) {
      const err = new Error(data.message || data.error || `Request failed (${res.status})`);
      err.data = data;
      throw err;
    }
    return data;
  }

  function formatBytes(bytes) {
    const n = Number(bytes) || 0;
    if (n <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0; let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
  }

  // Timestamps from the server are ISO strings in the server timezone (PRUNEMATE_TZ).
  // Show them as-is (wall-clock in that timezone) so they match the configured schedule.
  const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function formatDate(iso) {
    const m = ISO_RE.exec(iso || '');
    if (!m) return '–';
    const [, y, mo, d, hh, mi] = m;
    if (USE_24H) return `${d}-${mo}-${y} ${hh}:${mi}`;
    const h = parseInt(hh, 10);
    return `${d}-${mo}-${y} ${h % 12 || 12}:${mi} ${h >= 12 ? 'PM' : 'AM'}`;
  }

  function formatShortDate(iso) {
    const m = ISO_RE.exec(iso || '');
    return m ? `${parseInt(m[3], 10)} ${MONTHS[parseInt(m[2], 10) - 1]}` : '';
  }

  /** "in 5h 12m" / "3h ago" */
  function relativeTime(iso) {
    if (!iso) return '–';
    const diff = new Date(iso).getTime() - Date.now();
    if (Number.isNaN(diff)) return '–';
    const abs = Math.abs(diff);
    const mins = Math.round(abs / 60000);
    let text;
    if (mins < 1) text = diff >= 0 ? 'any moment' : 'just now';
    else {
      const d = Math.floor(mins / 1440);
      const h = Math.floor((mins % 1440) / 60);
      const m = mins % 60;
      if (d > 0) text = `${d}d ${h}h`;
      else if (h > 0) text = `${h}h ${m}m`;
      else text = `${m}m`;
      text = diff >= 0 ? `in ${text}` : `${text} ago`;
    }
    return text;
  }

  function originLabel(run) {
    return { scheduled: 'Scheduled', manual: 'Manual' }[run.origin] || 'Unknown';
  }

  function itemsOf(totals) {
    if (!totals) return 0;
    return ['containers', 'images', 'networks', 'volumes', 'build_cache']
      .reduce((sum, k) => sum + (Number(totals[k]) || 0), 0);
  }

  // ------------------------------------------------------------------
  // Toasts
  // ------------------------------------------------------------------
  function toast(message, type = 'info', timeout = 5000) {
    const stack = $('#toasts');
    if (!stack || !message) return;
    const iconName = type === 'success' ? 'check-circle' : type === 'error' ? 'x-circle' : 'info';
    const close = el('button', { type: 'button', class: 'toast-close', 'aria-label': 'Dismiss' }, [icon('x', 'icon-sm')]);
    const node = el('div', { class: `toast ${type}`, role: type === 'error' ? 'alert' : 'status' },
      [icon(iconName), el('div', { class: 'toast-msg', text: message }), close]);
    const remove = () => {
      node.classList.add('leaving');
      setTimeout(() => node.remove(), 220);
    };
    close.addEventListener('click', remove);
    stack.appendChild(node);
    if (timeout) setTimeout(remove, timeout);
  }

  // ------------------------------------------------------------------
  // Dialogs
  // ------------------------------------------------------------------
  function wireDialogClose(dialog) {
    $$('[data-close]', dialog).forEach(btn => btn.addEventListener('click', () => dialog.close()));
    // Click on the backdrop closes the dialog
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  }

  function confirmDialog({ title = 'Are you sure?', message = '', confirmText = 'Confirm', danger = true } = {}) {
    const dialog = $('#confirm-dialog');
    $('#confirm-title').textContent = title;
    $('#confirm-message').textContent = message;
    const ok = $('#confirm-ok');
    const cancel = $('#confirm-cancel');
    ok.textContent = confirmText;
    ok.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
    return new Promise((resolve) => {
      const done = (value) => {
        ok.removeEventListener('click', onOk);
        cancel.removeEventListener('click', onCancel);
        dialog.removeEventListener('close', onClose);
        if (dialog.open) dialog.close();
        resolve(value);
      };
      const onOk = () => done(true);
      const onCancel = () => done(false);
      const onClose = () => done(false);
      ok.addEventListener('click', onOk);
      cancel.addEventListener('click', onCancel);
      dialog.addEventListener('close', onClose);
      dialog.showModal();
      cancel.focus();
    });
  }

  // ------------------------------------------------------------------
  // Settings form: schedule, validation, provider switching
  // ------------------------------------------------------------------
  const form = $('#settings-form');

  function updateScheduleVisibility() {
    const freq = $('#frequency').value;
    $('#week-row').hidden = freq !== 'weekly';
    $('#month-row').hidden = freq !== 'monthly';
  }

  function clampNumberInput(input, min, max, pad = 0) {
    if (!input) return;
    const clamp = () => {
      const raw = input.value.trim();
      if (raw === '') return;
      let n = parseInt(raw.slice(0, 2 + String(max).length), 10);
      if (Number.isNaN(n)) n = min;
      n = Math.max(min, Math.min(max, n));
      input.value = pad ? String(n).padStart(pad, '0') : String(n);
    };
    input.addEventListener('blur', clamp);
    clamp();
  }

  function init12hTime() {
    if (USE_24H) return;
    const hidden = $('#time');
    const hour = $('#time-hour');
    const minute = $('#time-minute');
    const period = $('#time-period');
    if (!hidden || !hour || !minute || !period) return;
    const [h24s, ms] = (hidden.value || '03:00').split(':');
    const h24 = parseInt(h24s, 10) || 0;
    hour.value = h24 % 12 || 12;
    minute.value = String(parseInt(ms, 10) || 0).padStart(2, '0');
    period.value = h24 >= 12 ? 'PM' : 'AM';
    const sync = () => {
      const h12 = Math.max(1, Math.min(12, parseInt(hour.value, 10) || 12));
      const m = Math.max(0, Math.min(59, parseInt(minute.value, 10) || 0));
      let h = h12 % 12;
      if (period.value === 'PM') h += 12;
      hidden.value = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    };
    [hour, minute, period].forEach(i => i.addEventListener('change', sync));
    clampNumberInput(hour, 1, 12);
    clampNumberInput(minute, 0, 59, 2);
  }

  function updateProviderFields() {
    const provider = $('#notifications_provider').value;
    $$('.provider-fields').forEach(block => { block.hidden = block.dataset.provider !== provider; });
  }

  function updateCleanupHints() {
    $('#volume-warning').hidden = !$('#prune_volumes').checked;
    const protect = $('#protect_label_enabled').checked;
    $('#protect_label').disabled = !protect;
    $('#protect-label-field').classList.toggle('muted', !protect);
  }

  // Secret "Remove" buttons mark the stored value for deletion on save
  function initSecretFields() {
    $$('[data-action="clear-secret"]').forEach(btn => {
      btn.addEventListener('click', () => {
        const field = btn.dataset.field;
        const flag = form.querySelector(`input[name="${field}_clear"]`);
        const input = form.querySelector(`input[name="${field}"]`);
        const state = form.querySelector(`[data-secret-state="${field}"]`);
        const clearing = flag.value !== '1';
        flag.value = clearing ? '1' : '0';
        btn.textContent = clearing ? 'Undo' : 'Remove';
        input.value = '';
        input.disabled = clearing;
        state.className = 'secret-state ' + (clearing ? 'cleared' : 'saved');
        state.textContent = clearing ? 'Will be removed when you save' : 'Stored securely, not shown';
        markDirty();
      });
    });
  }

  // After a successful save, update secret placeholders so they reflect the stored state
  function refreshSecretStates() {
    $$('input[data-secret]').forEach(input => {
      const field = input.name;
      const flag = form.querySelector(`input[name="${field}_clear"]`);
      const state = form.querySelector(`[data-secret-state="${field}"]`);
      const wasCleared = flag.value === '1';
      const nowSet = !wasCleared && (input.value.trim() !== '' || state.classList.contains('saved'));
      input.value = '';
      input.disabled = false;
      flag.value = '0';
      input.placeholder = nowSet ? 'Saved – leave empty to keep' : '';
      state.className = 'secret-state' + (nowSet ? ' saved' : '');
      state.replaceChildren(...(nowSet ? [icon('lock', 'icon-sm'), 'Stored securely, not shown'] : ['Not set']));
      const btn = form.querySelector(`[data-action="clear-secret"][data-field="${field}"]`);
      if (btn) { btn.textContent = 'Remove'; btn.hidden = !nowSet; }
    });
  }

  // ------------------------------------------------------------------
  // Dirty tracking + sticky save bar
  // ------------------------------------------------------------------
  let baseline = '';

  function serializeForm() {
    const data = new FormData(form);
    data.delete('csrf_token');
    const pairs = [];
    for (const [k, v] of data.entries()) pairs.push(k + '=' + v);
    return pairs.sort().join('&');
  }

  function setBaseline() {
    baseline = serializeForm();
    markDirty();
  }

  function isDirty() { return serializeForm() !== baseline; }

  function markDirty() {
    const dirty = isDirty();
    $('#savebar').classList.toggle('dirty', dirty);
    $('#save-status-text').textContent = dirty ? 'Unsaved changes' : 'All changes saved';
    $('#save-btn').disabled = !dirty;
    $('#discard-btn').hidden = !dirty;
  }

  async function saveSettings(endpoint = form.action, successType = 'success') {
    const btn = $('#save-btn');
    btn.disabled = true;
    try {
      const data = await api(endpoint, { method: 'POST', body: new FormData(form) });
      toast(data.message || 'Saved.', data.success === false ? 'error' : successType);
      refreshSecretStates();
      setBaseline();
      loadDashboard();
      return true;
    } catch (err) {
      toast(err.message, 'error', 8000);
      markDirty();
      return false;
    }
  }

  // ------------------------------------------------------------------
  // Dashboard: tiles, counts, history chart & table
  // ------------------------------------------------------------------
  let dashboard = null;

  async function loadDashboard() {
    try {
      dashboard = await api('/api/dashboard');
      renderDashboard();
    } catch (err) {
      $('#next-run-rel').textContent = '–';
      $('#next-run-abs').textContent = 'Could not load dashboard';
    }
  }

  function renderTiles() {
    if (!dashboard) return;
    const s = dashboard.stats || {};
    const history = dashboard.history || [];

    // Next run
    if (!dashboard.schedule_enabled) {
      $('#next-run-rel').textContent = 'Paused';
      $('#next-run-abs').replaceChildren(el('span', { class: 'badge neutral', text: 'Schedule disabled' }));
    } else if (dashboard.next_run) {
      $('#next-run-rel').textContent = relativeTime(dashboard.next_run);
      $('#next-run-abs').textContent = formatDate(dashboard.next_run);
    }
    $('#next-run-abs').title = dashboard.schedule_text || '';

    // Last run
    const last = history[history.length - 1];
    const lastIso = last ? last.timestamp : s.last_run;
    $('#last-run-rel').textContent = lastIso ? relativeTime(lastIso) : 'Never';
    const detail = $('#last-run-detail');
    if (last) {
      const failed = (last.hosts || []).filter(h => !h.success).length;
      const badge = failed
        ? el('span', { class: 'badge bad' }, [icon('alert', 'icon-sm'), `${failed} host${failed > 1 ? 's' : ''} failed`])
        : el('span', { class: 'badge good' }, [icon('check', 'icon-sm'), 'OK']);
      const n = itemsOf(last.totals);
      detail.replaceChildren(badge, `${formatBytes(last.totals?.space)} · ${n} item${n === 1 ? '' : 's'}`);
    } else {
      detail.textContent = lastIso ? formatDate(lastIso) : 'No runs yet';
    }

    // Totals
    $('#stats-space').textContent = formatBytes(s.total_space_reclaimed);
    const runs = Number(s.prune_runs) || 0;
    $('#stats-runs-sub').textContent = runs
      ? `over ${runs} run${runs === 1 ? '' : 's'} since ${formatShortDate(s.first_run) || '–'}`
      : 'No runs yet';
    $('#stats-runs').textContent = runs;
    $('#stats-containers').textContent = s.containers_deleted || 0;
    $('#stats-images').textContent = s.images_deleted || 0;
    $('#stats-networks').textContent = s.networks_deleted || 0;
    $('#stats-volumes').textContent = s.volumes_deleted || 0;
    $('#stats-build-cache').textContent = s.build_cache_deleted || 0;
  }

  function renderHistoryTable(history) {
    const body = $('#history-table');
    if (!history.length) {
      body.replaceChildren(el('tr', {}, [el('td', { colspan: 6, class: 'muted', text: 'No runs yet.' })]));
      return;
    }
    const rows = history.slice(-10).reverse().map(run => {
      const failed = (run.hosts || []).filter(h => !h.success);
      const status = failed.length
        ? el('span', { class: 'badge bad', title: failed.map(h => `${h.name}: ${h.error || 'error'}`).join('\n') },
            [icon('alert', 'icon-sm'), `${failed.length} failed`])
        : el('span', { class: 'badge good' }, [icon('check', 'icon-sm'), 'OK']);
      return el('tr', {}, [
        el('td', { text: formatDate(run.timestamp) }),
        el('td', {}, [originLabel(run), run.imported ? ' ' : null, run.imported
          ? el('span',{ class: 'badge neutral', title: 'Reconstructed from V1 log files; space is approximate' }, 'from V1 logs')
          : null]),
        el('td', {}, [status]),
        el('td', { class: 'num', text: itemsOf(run.totals) }),
        el('td', { class: 'num', text: formatBytes(run.totals?.space) }),
        el('td', { class: 'num', text: run.duration != null ? `${run.duration}s` : '–' }),
      ]);
    });
    body.replaceChildren(...rows);
  }

  /** Single-series bar chart (space reclaimed per run) with hover tooltips. */
  function renderChart(history) {
    const box = $('#history-chart');
    const tooltip = $('#chart-tooltip');
    tooltip.hidden = true;
    const runs = history.slice(-30);
    $('#chart-caption').textContent = runs.length ? `Last ${runs.length} run${runs.length === 1 ? '' : 's'}` : '';

    if (!runs.length) {
      box.replaceChildren(el('div', { class: 'chart-empty' }, [
        icon('activity', 'icon-lg'),
        'No runs yet. Your first prune run will show up here.',
      ]));
      return;
    }

    const width = Math.max(240, box.clientWidth);
    const height = box.clientHeight || 170;
    const pad = { top: 10, right: 4, bottom: 22, left: 52 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const max = Math.max(...runs.map(r => Number(r.totals?.space) || 0), 1);

    // "Nice" upper bound in bytes for 3 gridlines
    const unit = Math.pow(1024, Math.floor(Math.log(max) / Math.log(1024)));
    const step = Math.ceil(max / unit / 3) || 1;
    const top = step * 3 * unit;
    const y = (v) => pad.top + plotH - (v / top) * plotH;

    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
      'aria-label': `Bar chart of space reclaimed for the last ${runs.length} runs. See "Recent runs" for a table.` });

    for (let i = 0; i <= 3; i++) {
      const v = step * i * unit;
      const yy = y(v);
      svg.appendChild(svgEl('line', { class: 'grid-line', x1: pad.left, x2: width - pad.right, y1: yy, y2: yy }));
      const label = svgEl('text', { class: 'axis-label', x: pad.left - 8, y: yy + 3, 'text-anchor': 'end' });
      label.textContent = formatBytes(v);
      svg.appendChild(label);
    }

    const slot = plotW / runs.length;
    const barW = Math.max(3, Math.min(28, slot - 2));
    const radius = Math.min(4, barW / 2);
    const baseY = y(0);

    runs.forEach((run, i) => {
      const value = Number(run.totals?.space) || 0;
      const x = pad.left + i * slot + (slot - barW) / 2;
      const h = value > 0 ? Math.max(2, baseY - y(value)) : 2;
      const yTop = baseY - h;
      const r = Math.min(radius, h);
      // Rounded top corners, square base anchored to the baseline
      const d = `M${x},${baseY} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + barW - r} Q${x + barW},${yTop} ${x + barW},${yTop + r} V${baseY} Z`;

      const hit = svgEl('rect', { class: 'hit', x: pad.left + i * slot, y: pad.top, width: slot, height: plotH });
      const bar = svgEl('path', { class: 'bar' + (value > 0 ? '' : ' zero'), d });
      hit.addEventListener('mouseenter', () => showTooltip(run, bar, x + barW / 2, yTop, width));
      hit.addEventListener('mouseleave', () => { tooltip.hidden = true; bar.classList.remove('active'); });
      svg.appendChild(hit);
      svg.appendChild(bar);
    });

    // Sparse x labels (first, middle, last)
    const labelIdx = new Set([0, Math.floor((runs.length - 1) / 2), runs.length - 1]);
    labelIdx.forEach(i => {
      const t = svgEl('text', { class: 'axis-label', x: pad.left + i * slot + slot / 2, y: height - 6, 'text-anchor': 'middle' });
      t.textContent = formatShortDate(runs[i].timestamp);
      svg.appendChild(t);
    });

    box.replaceChildren(svg);
  }

  function showTooltip(run, bar, cx, yTop, chartWidth) {
    const tooltip = $('#chart-tooltip');
    const t = run.totals || {};
    const failed = (run.hosts || []).filter(h => !h.success).length;
    const row = (label, value) => el('div', { class: 'tt-row' }, [label, el('b', { text: value })]);
    tooltip.replaceChildren(...[
      el('div', { class: 'tt-title', text: formatDate(run.timestamp) }),
      row('Reclaimed', formatBytes(t.space)),
      row('Items removed', String(itemsOf(t))),
      row('Trigger', originLabel(run)),
      row('Status', failed ? `${failed} host(s) failed` : 'OK'),
      run.imported ? row('Source', 'V1 logs (approx.)') : null,
    ].filter(Boolean));
    tooltip.hidden = false;
    bar.classList.add('active');
    const box = $('#history-chart');
    const scale = box.clientWidth / chartWidth;
    const tw = tooltip.offsetWidth;
    let left = box.offsetLeft + cx * scale - tw / 2;
    left = Math.max(box.offsetLeft, Math.min(left, box.offsetLeft + box.clientWidth - tw));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${box.offsetTop + Math.max(0, yTop * scale - tooltip.offsetHeight - 8)}px`;
  }

  function renderDashboard() {
    if (!dashboard) return;
    renderTiles();
    const history = dashboard.history || [];
    renderChart(history);
    renderHistoryTable(history);
  }

  // ------------------------------------------------------------------
  // Docker hosts
  // ------------------------------------------------------------------
  let hostStatusBusy = false;

  function hostRow(host, index, isLocal) {
    const dot = el('span', {
      class: 'status-dot ' + (isLocal ? 'online' : host.enabled ? 'checking' : 'disabled'),
      title: isLocal ? 'Always connected' : host.enabled ? 'Checking Docker connection…' : 'Disabled',
      dataset: isLocal ? {} : { hostIndex: String(index) },
    });
    const name = el('div', { class: 'host-name' }, [host.name]);
    if (isLocal) name.append(el('span', { class: 'badge neutral', text: 'local' }));
    if (host.tls) name.append(el('span', { class: 'badge good', title: `TLS certificates: /certs/${host.tls}` }, [icon('lock', 'icon-sm'), 'TLS']));
    if (!isLocal && !host.enabled) name.append(el('span', { class: 'badge neutral', text: 'disabled' }));

    const row = el('div', { class: 'host-row' + (!isLocal && !host.enabled ? ' disabled' : '') }, [
      dot,
      el('div', { class: 'host-info' }, [name, el('div', { class: 'host-url', text: host.url, title: host.url })]),
    ]);
    if (!isLocal) {
      row.append(el('div', { class: 'host-actions' }, [
        el('button', { type: 'button', class: 'btn-icon', title: host.enabled ? 'Disable' : 'Enable',
          'aria-label': `${host.enabled ? 'Disable' : 'Enable'} ${host.name}`, onclick: () => toggleHost(index) },
        [icon(host.enabled ? 'pause' : 'play', 'icon-sm')]),
        el('button', { type: 'button', class: 'btn-icon', title: 'Edit', 'aria-label': `Edit ${host.name}`,
          onclick: () => editHost(index, host) }, [icon('edit', 'icon-sm')]),
        el('button', { type: 'button', class: 'btn-icon danger', title: 'Delete', 'aria-label': `Delete ${host.name}`,
          onclick: () => deleteHost(index, host.name) }, [icon('trash', 'icon-sm')]),
      ]));
    }
    return row;
  }

  async function loadHosts() {
    const list = $('#hosts-list');
    try {
      const data = await api('/hosts');
      const hosts = data.hosts || [];
      const [local, ...external] = hosts;
      const rows = [hostRow(local, -1, true), ...external.map((h, i) => hostRow(h, i, false))];
      if (!external.length) rows.push(el('div', { class: 'host-empty', text: 'No external hosts yet.' }));
      list.replaceChildren(...rows);
      const enabled = external.filter(h => h.enabled).length;
      $('#hosts-summary').textContent = external.length
        ? `${external.length} external host${external.length === 1 ? '' : 's'} (${enabled} enabled) + local`
        : 'Only the local Docker host is managed';
      refreshHostStatuses();
    } catch (err) {
      list.replaceChildren(el('div', { class: 'host-empty', text: 'Failed to load hosts: ' + err.message }));
    }
  }

  async function refreshHostStatuses() {
    const dots = $$('#hosts-list .status-dot[data-host-index]');
    if (!dots.length || hostStatusBusy) return;
    hostStatusBusy = true;
    const titles = { online: 'Online', offline: 'Offline: Docker ping failed', disabled: 'Disabled' };
    try {
      const data = await api('/hosts/status');
      const byIndex = new Map(dots.map(d => [Number(d.dataset.hostIndex), d]));
      (data.statuses || []).forEach(s => {
        const dot = byIndex.get(s.index);
        if (!dot || !titles[s.status]) return;
        dot.className = 'status-dot ' + s.status;
        dot.title = titles[s.status];
      });
    } catch (_) {
      dots.forEach(d => { if (d.classList.contains('checking')) d.title = 'Status unavailable'; });
    } finally {
      hostStatusBusy = false;
    }
  }

  function hostFormData(name, url, tls, enabled = true) {
    const fd = new FormData();
    fd.append('name', name.trim());
    fd.append('url', url.trim());
    fd.append('tls', (tls || '').trim());
    fd.append('enabled', enabled ? 'on' : 'off');
    return fd;
  }

  async function addHost() {
    const name = $('#new-host-name');
    const url = $('#new-host-url');
    const tls = $('#new-host-tls');
    if (!name.value.trim() || !url.value.trim()) {
      toast('Please enter both a host name and URL.', 'error');
      (name.value.trim() ? url : name).focus();
      return;
    }
    try {
      const data = await api('/hosts/add', { method: 'POST', body: hostFormData(name.value, url.value, tls.value) });
      toast(data.message, 'success');
      name.value = ''; url.value = ''; tls.value = '';
      loadHosts();
    } catch (err) {
      toast(err.message, 'error', 8000);
    }
  }

  async function toggleHost(index) {
    try {
      const data = await api(`/hosts/${index}/toggle`, { method: 'POST' });
      toast(data.message, 'success', 3000);
      loadHosts();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function editHost(index, host) {
    const dialog = $('#host-dialog');
    const formEl = $('#host-form');
    $('#edit-host-name').value = host.name;
    $('#edit-host-url').value = host.url;
    $('#edit-host-tls').value = host.tls || '';
    const onSubmit = async (e) => {
      e.preventDefault();
      try {
        const data = await api(`/hosts/${index}/update`, {
          method: 'POST',
          body: hostFormData($('#edit-host-name').value, $('#edit-host-url').value, $('#edit-host-tls').value, host.enabled),
        });
        dialog.close();
        toast(data.message, 'success');
        loadHosts();
      } catch (err) {
        toast(err.message, 'error', 8000);
      }
    };
    formEl.onsubmit = onSubmit;
    dialog.showModal();
    $('#edit-host-name').focus();
  }

  async function deleteHost(index, name) {
    const ok = await confirmDialog({
      title: 'Delete host?',
      message: `"${name}" will be removed from PruneMate. Nothing on the host itself is changed.`,
      confirmText: 'Delete host',
    });
    if (!ok) return;
    try {
      const data = await api(`/hosts/${index}/delete`, { method: 'POST' });
      toast(data.message, 'success');
      loadHosts();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ------------------------------------------------------------------
  // Prune preview & run
  // ------------------------------------------------------------------
  const RESOURCE_TYPES = [
    { key: 'containers', label: 'Containers', icon: 'container', item: c => [c.name || c.id, el('span', { class: 'meta', text: ` (${c.status})` })] },
    { key: 'images', label: 'Images', icon: 'image', item: i => [(i.tags || []).join(', ') || i.id, el('span', { class: 'meta', text: ` (${i.size})` })] },
    { key: 'networks', label: 'Networks', icon: 'network', item: n => [n.name] },
    { key: 'volumes', label: 'Volumes', icon: 'volume', item: v => [v.name, el('span', { class: 'meta', text: ` (${v.driver})` })] },
    { key: 'build_cache', label: 'Build cache', icon: 'build', item: b => [b.id, el('span', { class: 'meta', text: ` (${b.type}, ${b.size})` })] },
  ];

  function pruneSelection() {
    return {
      prune_containers: $('#prune_containers').checked,
      prune_images: $('#prune_images').checked,
      prune_networks: $('#prune_networks').checked,
      prune_volumes: $('#prune_volumes').checked,
      prune_build_cache: $('#prune_build_cache').checked,
    };
  }

  function stateBox(kind, iconName, title, detail) {
    return el('div', { class: `state-box ${kind}` }, [
      kind === 'loading' ? el('span', { class: 'spinner' }) : icon(iconName),
      el('div', { class: 'state-title', text: title }),
      detail ? el('div', { text: detail }) : null,
    ]);
  }

  function summaryGrid(totals, extra = []) {
    const items = RESOURCE_TYPES.filter(t => (totals[t.key] || 0) > 0).map(t =>
      el('div', { class: 'summary-item' }, [
        el('div', { class: 'v', text: totals[t.key] }),
        el('div', { class: 'l' }, [icon(t.icon, 'icon-sm'), t.label]),
      ]));
    return el('div', { class: 'summary-grid' }, [...items, ...extra]);
  }

  async function openPreview() {
    if (isDirty()) {
      const save = await confirmDialog({
        title: 'Save changes first?',
        message: 'You have unsaved settings. The preview and run use your saved settings, so they will be saved now.',
        confirmText: 'Save & continue',
        danger: false,
      });
      if (!save) return;
      if (!(await saveSettings())) return;
    }

    const dialog = $('#preview-dialog');
    const body = $('#preview-body');
    const confirmBtn = $('#preview-confirm');
    const volBox = $('#volume-confirm');
    const volInput = $('#volume-confirm-input');
    confirmBtn.hidden = true;
    confirmBtn.disabled = false;
    volBox.hidden = true;
    volInput.value = '';
    $('#preview-cancel').textContent = 'Cancel';
    body.replaceChildren(stateBox('loading', null, 'Collecting preview…', 'Checking all enabled Docker hosts.'));
    dialog.showModal();

    let data;
    try {
      data = await api('/preview-prune', { method: 'POST', json: pruneSelection() });
    } catch (err) {
      body.replaceChildren(stateBox('bad', 'x-circle', 'Failed to load preview', err.message));
      return;
    }
    if (data.error) {
      body.replaceChildren(stateBox('bad', 'alert', data.error, 'Enable at least one cleanup option.'));
      return;
    }

    const totals = data.totals || {};
    const total = itemsOf(totals);
    const nodes = [];

    const protections = [];
    if (data.protect_label) protections.push(`label "${data.protect_label}"`);
    if (data.min_age_hours) protections.push(`younger than ${data.min_age_hours}h`);
    if (data.protected) {
      nodes.push(el('div', { class: 'callout info' }, [icon('shield'),
        el('div', { text: `${data.protected} resource(s) are skipped by your protection rules (${protections.join(', ') || 'rules'}).` })]));
    }

    if (!total) {
      nodes.push(stateBox('good', 'check-circle', 'Nothing to prune!', 'Your Docker resources are already clean.'));
      (data.hosts || []).filter(h => !h.success).forEach(h => nodes.push(hostErrorBlock(h)));
      body.replaceChildren(...nodes);
      return;
    }

    nodes.push(summaryGrid(totals));
    (data.hosts || []).forEach(host => {
      if (!host.success) { nodes.push(hostErrorBlock(host)); return; }
      if (!itemsOf(host.totals)) return;
      const block = el('div', { class: 'host-block' }, [el('h4', {}, [icon('server', 'icon-sm'), host.name])]);
      RESOURCE_TYPES.forEach(t => {
        const list = host[t.key] || [];
        if (!list.length) return;
        const shown = list.slice(0, 25);
        const ul = el('ul', { class: 'res-list' }, shown.map(item => el('li', {}, t.item(item))));
        if (list.length > shown.length) ul.append(el('li', { class: 'meta', text: `… and ${list.length - shown.length} more` }));
        block.append(el('div', { class: 'res-group' }, [
          el('div', { class: 'res-group-title' }, [icon(t.icon, 'icon-sm'), `${t.label} (${list.length})`]), ul,
        ]));
      });
      nodes.push(block);
    });

    const volumeCount = totals.volumes || 0;
    if (volumeCount > 0) {
      nodes.unshift(el('div', { class: 'callout danger' }, [icon('alert'),
        el('div', { text: `${volumeCount} volume(s) and all data in them will be deleted permanently. Check the list below carefully.` })]));
      volBox.hidden = false;
      confirmBtn.disabled = true;
      volInput.oninput = () => { confirmBtn.disabled = volInput.value.trim() !== 'DELETE'; };
    }

    body.replaceChildren(...nodes);
    confirmBtn.hidden = false;
    confirmBtn.className = 'btn ' + (volumeCount > 0 ? 'btn-danger' : 'btn-primary');
    confirmBtn.onclick = () => runPrune(volumeCount > 0 ? volInput.value.trim() : '');
    if (volumeCount > 0) volInput.focus(); else confirmBtn.focus();
  }

  function hostErrorBlock(host) {
    return el('div', { class: 'host-block error' }, [
      el('h4', {}, [icon('x-circle', 'icon-sm'), host.name]),
      el('div', { class: 'muted', text: host.error || 'Failed to connect' }),
    ]);
  }

  async function runPrune(confirmVolumes) {
    const body = $('#preview-body');
    const confirmBtn = $('#preview-confirm');
    const cancelBtn = $('#preview-cancel');
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    $('#volume-confirm').hidden = true;
    body.replaceChildren(stateBox('loading', null, 'Pruning…', 'This can take a while on large hosts.'));

    try {
      const data = await api('/run-confirmed', {
        method: 'POST',
        json: { ...pruneSelection(), confirm_volumes: confirmVolumes },
      });
      if (!data.success || !data.run) {
        body.replaceChildren(stateBox('bad', 'alert', data.message || 'Prune job was skipped.'));
      } else {
        const run = data.run;
        const failed = (run.hosts || []).filter(h => !h.success);
        const nodes = [
          stateBox(failed.length ? 'bad' : 'good', failed.length ? 'alert' : 'check-circle',
            failed.length ? 'Prune finished with errors' : 'Prune completed',
            `${formatBytes(run.totals?.space)} reclaimed in ${run.duration}s`),
        ];
        if (itemsOf(run.totals)) nodes.push(summaryGrid(run.totals));
        failed.forEach(h => nodes.push(hostErrorBlock(h)));
        body.replaceChildren(...nodes);
        toast(`Prune completed: ${formatBytes(run.totals?.space)} reclaimed.`, failed.length ? 'error' : 'success');
      }
      confirmBtn.hidden = true;
      loadDashboard();
    } catch (err) {
      body.replaceChildren(stateBox('bad', 'x-circle', 'Execution failed', err.message));
      confirmBtn.disabled = false;
    } finally {
      cancelBtn.disabled = false;
      cancelBtn.textContent = 'Close';
    }
  }

  // ------------------------------------------------------------------
  // Log viewer
  // ------------------------------------------------------------------
  let logLines = [];

  async function loadLogs() {
    const out = $('#logs-output');
    out.textContent = 'Loading…';
    try {
      const data = await api(`/api/logs?lines=${encodeURIComponent($('#logs-lines').value)}`);
      logLines = data.lines || [];
      if (!logLines.length && data.message) { out.textContent = data.message; return; }
      renderLogs(true);
    } catch (err) {
      out.textContent = 'Failed to load logs: ' + err.message;
    }
  }

  function renderLogs(scrollToEnd = false) {
    const out = $('#logs-output');
    const filter = $('#logs-filter').value.trim().toLowerCase();
    const hideHeartbeat = $('#logs-hide-heartbeat').checked;
    const lines = logLines.filter(line =>
      (!hideHeartbeat || !line.includes('Heartbeat:')) && (!filter || line.toLowerCase().includes(filter)));
    if (!lines.length) { out.textContent = 'No matching log lines.'; return; }
    const frag = document.createDocumentFragment();
    lines.forEach(line => {
      const lower = line.toLowerCase();
      let cls = 'log-line';
      if (/error|failed|rejected/.test(lower)) cls += ' err';
      else if (/warning|skipp/.test(lower)) cls += ' warn';
      else if (/completed|successful|sent/.test(lower)) cls += ' ok';
      frag.append(el('div', { class: cls, text: line }));
    });
    out.replaceChildren(frag);
    if (scrollToEnd) out.scrollTop = out.scrollHeight;
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', () => {
    $$('dialog.modal').forEach(wireDialogClose);

    // Schedule & form helpers
    $('#frequency').addEventListener('change', updateScheduleVisibility);
    updateScheduleVisibility();
    clampNumberInput($('#day_of_month'), 1, 31);
    clampNumberInput($('#min_age_hours'), 0, 87600);
    init12hTime();
    $('#notifications_provider').addEventListener('change', updateProviderFields);
    updateProviderFields();
    ['#prune_volumes', '#protect_label_enabled'].forEach(s => $(s).addEventListener('change', updateCleanupHints));
    updateCleanupHints();
    initSecretFields();

    // Dirty tracking (inputs inside [data-no-dirty] have no name and aren't serialized)
    setBaseline();
    form.addEventListener('input', markDirty);
    form.addEventListener('change', markDirty);
    window.addEventListener('beforeunload', (e) => {
      if (isDirty()) { e.preventDefault(); e.returnValue = ''; }
    });

    form.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!form.reportValidity()) return;
      saveSettings();
    });
    $('[data-action="discard"]').addEventListener('click', async () => {
      const ok = await confirmDialog({ title: 'Discard changes?', message: 'All unsaved changes will be lost.', confirmText: 'Discard' });
      if (ok) { baseline = serializeForm(); window.location.reload(); }
    });
    $('[data-action="test-notification"]').addEventListener('click', () => {
      if (!form.reportValidity()) return;
      saveSettings('/test-notification', 'info');
    });
    $('[data-action="preview"]').addEventListener('click', openPreview);

    // Hosts
    $('[data-action="add-host"]').addEventListener('click', addHost);
    ['#new-host-name', '#new-host-url', '#new-host-tls'].forEach(s =>
      $(s).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addHost(); } }));
    loadHosts();
    setInterval(refreshHostStatuses, 60000);

    // Dashboard
    loadDashboard();
    setInterval(loadDashboard, 60000);
    setInterval(renderTiles, 20000); // keep relative times fresh
    if ('ResizeObserver' in window) {
      let raf = 0;
      new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => dashboard && renderChart(dashboard.history || []));
      }).observe($('#history-chart'));
    }

    // Logs
    $('[data-action="open-logs"]').addEventListener('click', () => { $('#logs-dialog').showModal(); loadLogs(); });
    $('#logs-refresh').addEventListener('click', loadLogs);
    $('#logs-lines').addEventListener('change', loadLogs);
    $('#logs-filter').addEventListener('input', () => renderLogs(false));
    $('#logs-hide-heartbeat').addEventListener('change', () => renderLogs(true));

    // Server-side flash messages (non-JS fallback paths)
    const flash = document.body.dataset.flash;
    if (flash) toast(flash, document.body.dataset.flashType === 'warn' ? 'error' : 'info');
  });
})();
