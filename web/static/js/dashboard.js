/**
 * Multiviewer dashboard - main UI logic
 */
let canvas;
let state = { zones: [], display: { width: 1920, height: 1080 }, audio_zone: null };
let files = [];
let testSources = [];
const $ = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', async () => {
    canvas = new CanvasController('layout-canvas');
    canvas.onSelect = (z) => fillEditor(z, true);
    canvas.onChange = (z) => fillGeometry(z.geometry);
    canvas.onCommit = (z) => run(() => apiClient.updateZone(z.id, { geometry: z.geometry }), false);
    bindUI();
    await Promise.all([loadTemplates(), loadFiles(), loadPresets(), loadTestSources()]);
    await refresh();
    setInterval(refresh, 1000);
});

// ------------------------------------------------------------------ helpers
function toast(msg, isError = true) {
    const t = $('toast');
    t.textContent = msg;
    t.className = 'toast show' + (isError ? ' error' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.className = 'toast'), 3500);
}

async function run(fn, doRefresh = true) {
    try {
        const r = await fn();
        if (doRefresh) await refresh();
        return r;
    } catch (e) {
        toast(e.message);
        await refresh();
    }
}

const selectedZone = () => state.zones.find((z) => z.id === canvas.selectedId);
const sel = () => canvas.selectedId;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const shortSrc = (s) => (s ? s.replace(/^(\w+:\/\/)([^@/]*@)?/, '$1') : '—');

// ------------------------------------------------------------------ binding
function bindUI() {
    $('play-all').onclick = () => run(() => apiClient.playAll());
    $('stop-all').onclick = () => run(() => apiClient.stopAll());
    $('snap').onchange = (e) => (canvas.snap = parseInt(e.target.value, 10));

    $('update-resolution').onclick = () => run(async () => {
        const w = parseInt($('display-width').value, 10), h = parseInt($('display-height').value, 10);
        await apiClient.setDisplayResolution(w, h);
        canvas.setDisplayResolution(w, h);
    });

    $('apply-grid').onclick = () => run(() => apiClient.applyGrid(
        parseInt($('grid-cols').value, 10), parseInt($('grid-rows').value, 10), parseInt($('grid-gap').value, 10)));

    $('add-zone').onclick = () => run(async () => {
        const z = await apiClient.addZone({});
        await refresh();
        canvas.select(z.id);
    });

    // --- zone editor
    const patch = (data) => sel() !== null && run(() => apiClient.updateZone(sel(), data));
    $('z-name').onchange = (e) => patch({ name: e.target.value });
    $('z-id').onchange = (e) => run(async () => {
        const z = await apiClient.post(`/api/zones/${sel()}/renumber`, { id: parseInt(e.target.value, 10) });
        canvas.selectedId = z.id;
    });
    $('renumber').onclick = () => run(() => apiClient.post('/api/zones/renumber-by-position'));
    $('z-source').onchange = (e) => patch({ source: e.target.value.trim() || null });
    $('z-show-label').onchange = (e) => patch({ show_label: e.target.checked });
    $('z-label-pos').onchange = (e) => patch({ label_position: e.target.value });
    $('z-low-latency').onchange = (e) => patch({ low_latency: e.target.checked });
    $('z-low-cpu').onchange = (e) => patch({ low_cpu: e.target.checked });
    $('z-loop').onchange = (e) => patch({ loop: e.target.checked });
    $('z-hwdec').onchange = (e) => patch({ hwdec: e.target.value });
    const setting = (data) => run(() => apiClient.post('/api/settings', data));
    $('s-hwdec').onchange = (e) => setting({ hwdec: e.target.value });
    $('s-threads').onchange = (e) => setting({ decoder_threads: parseInt(e.target.value, 10) });
    $('s-nice').onchange = (e) => setting({ nice: parseInt(e.target.value, 10) });
    $('z-audio').onchange = (e) => run(() => apiClient.audioFollow(e.target.checked ? sel() : null));
    $('z-volume').oninput = (e) => ($('z-volume-value').textContent = e.target.value);
    $('z-volume').onchange = (e) => run(() => apiClient.setVolume(sel(), parseInt(e.target.value, 10)));

    ['z-x', 'z-y', 'z-w', 'z-h'].forEach((id) => ($(id).onchange = () => patch({ geometry: {
        x: +$('z-x').value, y: +$('z-y').value, width: +$('z-w').value, height: +$('z-h').value } })));

    $('z-tally').querySelectorAll('button').forEach((b) =>
        (b.onclick = () => run(() => apiClient.setTally(sel(), b.dataset.v))));

    $('z-play').onclick = () => run(async () => {
        const source = $('z-source').value.trim();
        if (!source) throw new Error('Enter a source first');
        await apiClient.playZone(sel(), { source, name: $('z-name').value });
    });
    $('z-pause').onclick = () => run(() => apiClient.pauseZone(sel()));
    $('z-stop').onclick = () => run(() => apiClient.stopZone(sel()));
    $('z-raise').onclick = () => run(() => apiClient.moveLayer(sel(), 'top'));
    $('z-lower').onclick = () => run(() => apiClient.moveLayer(sel(), 'bottom'));
    $('z-full').onclick = () => patch({ geometry: { x: 0, y: 0, width: state.display.width, height: state.display.height } });
    $('z-delete').onclick = () => {
        const z = selectedZone();
        if (z && confirm(`Delete tile ${z.id} "${z.name}"?`)) run(() => apiClient.deleteZone(z.id));
    };

    // --- presets & files
    $('save-preset').onclick = () => run(async () => {
        const name = $('preset-name').value.trim();
        if (!name) throw new Error('Enter a preset name');
        await apiClient.savePreset(name, $('preset-description').value.trim());
        $('preset-name').value = ''; $('preset-description').value = '';
        toast(`Preset "${name}" saved`, false);
        await loadPresets();
    });
    $('upload-btn').onclick = () => $('file-input').click();
    $('file-input').onchange = handleUpload;
}

// ------------------------------------------------------------------ refresh
async function refresh() {
    try {
        state = await apiClient.getStatus();
    } catch (e) {
        $('status-text').textContent = 'offline';
        $('status-indicator').className = 'status-stopped';
        return;
    }
    if (canvas.displayWidth !== state.display.width || canvas.displayHeight !== state.display.height) {
        canvas.setDisplayResolution(state.display.width, state.display.height);
        $('display-width').value = state.display.width;
        $('display-height').value = state.display.height;
    }
    canvas.setZones(state.zones);
    renderTable();
    renderHeader();
    renderSystem();
    if (state.settings) {
        setVal('s-hwdec', state.settings.hwdec, false);
        setVal('s-threads', state.settings.decoder_threads, false);
        setVal('s-nice', state.settings.nice, false);
    }
    fillEditor(selectedZone(), false);
}

function renderHeader() {
    const live = state.zones.filter((z) => z.status === 'playing').length;
    const bad = state.zones.some((z) => z.health === 'reconnecting' || z.health === 'lost' || z.status === 'error');
    $('status-text').textContent = `${live} / ${state.zones.length} live` + (bad ? ' • ⚠ signal problem' : '');
    $('status-indicator').className = bad ? 'status-paused' : (live ? 'status-playing' : 'status-stopped');
}

function decoderText(d) {
    if (!d || !d.width) return '–';
    const hw = d.hwdec ? `<span class="pill pill-playing" title="${esc(d.hwdec)}">HW</span>`
                       : '<span class="pill pill-error" title="software decoding">SW</span>';
    const drops = (d.decoder_drops || 0) + (d.vo_drops || 0);
    const fps = d.fps ? Math.round(d.fps) : '?';
    return `${hw} ${esc(d.codec || '')} ${d.width}×${d.height}@${fps}${drops ? ` <span class="warn" title="dropped frames">⚠${drops}</span>` : ''}`;
}

function renderSystem() {
    const s = state.system || {};
    const parts = [];
    if (s.load !== undefined) {
        const hot = s.load > (s.cores || 1);
        parts.push(`<span class="${hot ? 'warn' : ''}">CPU ${s.load}/${s.cores}</span>`);
    }
    if (s.temp_c !== undefined) parts.push(`<span class="${s.temp_c >= 75 ? 'warn' : ''}">${s.temp_c}°C</span>`);
    if (s.mem_avail_mb !== undefined) parts.push(`<span class="${s.mem_avail_mb < 300 ? 'warn' : ''}">RAM ${s.mem_avail_mb} MB free</span>`);
    if (s.undervoltage_now) parts.push('<span class="warn">⚡ UNDERVOLTAGE</span>');
    else if (s.undervoltage_since_boot) parts.push('<span class="warn" title="undervoltage occurred since boot">⚡ undervoltage seen</span>');
    if (s.throttled_now || s.soft_temp_limit_now) parts.push('<span class="warn">🔥 THROTTLED</span>');
    $('sys-info').innerHTML = parts.join(' · ');
}

function renderTable() {
    const body = $('zone-table-body');
    const rows = [...state.zones].sort((a, b) => a.id - b.id).map((z) => {
        const color = canvas.colorFor(z.id);
        const st = z.health === 'reconnecting' || z.health === 'lost' ? z.health : z.status;
        return `<tr data-id="${z.id}" class="${z.id === canvas.selectedId ? 'selected' : ''}">
            <td><span class="swatch" style="background:${color}"></span>${z.id}</td>
            <td>${esc(z.name)}</td>
            <td class="src" title="${esc(shortSrc(z.source))}">${esc(shortSrc(z.source))}</td>
            <td class="dec">${decoderText(z.decoder)}</td>
            <td><span class="pill pill-${st}">${st}</span></td>
            <td><input type="radio" name="audio-follow" data-audio="${z.id}" ${z.audio ? 'checked' : ''}></td>
            <td><button class="btn btn-icon tally-${z.tally}" data-tally="${z.id}" title="Cycle tally">${{ off: '–', preview: 'PVW', program: 'PGM', standby: 'STB' }[z.tally]}</button></td>
            <td class="row-actions">
                <button class="btn btn-icon" data-play="${z.id}" title="Play">▶</button>
                <button class="btn btn-icon" data-stop="${z.id}" title="Stop">⏹</button>
            </td></tr>`;
    }).join('');
    if (body._last !== rows) {
        body.innerHTML = rows || '<tr><td colspan="8" class="muted">No tiles yet – pick a template or add a tile.</td></tr>';
        body._last = rows;
        body.querySelectorAll('tr[data-id]').forEach((tr) => (tr.onclick = (e) => {
            if (e.target.closest('button,input')) return;
            canvas.select(+tr.dataset.id); renderTable();
        }));
        body.querySelectorAll('[data-audio]').forEach((r) => (r.onchange = () => run(() => apiClient.audioFollow(+r.dataset.audio))));
        body.querySelectorAll('[data-play]').forEach((b) => (b.onclick = () => run(() => apiClient.playZone(+b.dataset.play))));
        body.querySelectorAll('[data-stop]').forEach((b) => (b.onclick = () => run(() => apiClient.stopZone(+b.dataset.stop))));
        body.querySelectorAll('[data-tally]').forEach((b) => (b.onclick = () => {
            const z = state.zones.find((x) => x.id === +b.dataset.tally);
            const next = { off: 'preview', preview: 'program', program: 'off', standby: 'off' }[z.tally];
            run(() => apiClient.setTally(z.id, next));
        }));
    }
}

// ------------------------------------------------------------------ editor
function setVal(id, v, force) {
    const el = $(id);
    if (!force && document.activeElement === el) return;   // don't overwrite while typing
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? '';
}

function fillGeometry(g) {
    setVal('z-x', g.x, true); setVal('z-y', g.y, true); setVal('z-w', g.width, true); setVal('z-h', g.height, true);
}

function fillEditor(z, force) {
    $('zone-editor-empty').style.display = z ? 'none' : 'block';
    $('zone-editor-body').style.display = z ? 'block' : 'none';
    $('zone-editor-id').textContent = z ? `#${z.id}` : '';
    if (!z) return;
    setVal('z-id', z.id, force);
    setVal('z-name', z.name, force);
    setVal('z-source', z.source, force);
    setVal('z-show-label', z.show_label, force);
    setVal('z-label-pos', z.label_position, force);
    setVal('z-low-latency', z.low_latency, force);
    setVal('z-low-cpu', z.low_cpu, force);
    setVal('z-loop', z.loop, force);
    setVal('z-hwdec', z.hwdec || '', force);
    $('z-decoder').innerHTML = z.decoder && z.decoder.width
        ? `${decoderText(z.decoder)} ${z.decoder.hwdec ? '(' + esc(z.decoder.hwdec) + ')' : '– software decoding'}` : '';
    setVal('z-audio', z.audio, force);
    if (force || document.activeElement !== $('z-volume')) {
        $('z-volume').value = z.volume; $('z-volume-value').textContent = z.volume;
    }
    if (!canvas.busy) ['x', 'y', 'width', 'height'].forEach((k, i) =>
        setVal(['z-x', 'z-y', 'z-w', 'z-h'][i], z.geometry[k], force));
    $('z-tally').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === z.tally));
    $('z-pause').textContent = z.status === 'paused' ? '▶ Resume' : '⏸';
    $('z-error').textContent = z.status === 'error' && z.error ? `⚠ ${z.error}` : '';
}

// ------------------------------------------------------------------ templates / presets / files
async function loadTemplates() {
    try {
        const t = await apiClient.getTemplates();
        $('template-buttons').innerHTML = Object.entries(t).map(([name, n]) =>
            `<button class="btn btn-secondary btn-sm" data-template="${name}" title="${n} tiles">${name}</button>`).join('');
        $('template-buttons').querySelectorAll('button').forEach((b) => (b.onclick = () =>
            run(() => apiClient.applyTemplate(b.dataset.template, parseInt($('grid-gap').value, 10)))));
    } catch (e) { toast(e.message); }
}

async function loadTestSources() {
    try { testSources = await apiClient.getTestSources(); } catch { testSources = []; }
    renderDatalist();
}

function renderDatalist() {
    $('source-list').innerHTML = [...files.map((f) => f.name), ...testSources]
        .map((v) => `<option value="${esc(v)}">`).join('');
}

async function loadPresets() {
    try {
        const { presets, default: def } = await apiClient.listPresets();
        const entries = Object.entries(presets);
        $('preset-list').innerHTML = entries.length ? entries.map(([name, p]) => `
            <div class="preset-item">
                <div class="preset-info">
                    <div class="preset-name">${name === def ? '⭐ ' : ''}${esc(name)} <span class="muted">(${p.zones} tiles)</span></div>
                    ${p.description ? `<div class="preset-description">${esc(p.description)}</div>` : ''}
                </div>
                <div class="preset-actions">
                    <button class="btn btn-primary btn-icon" data-load="${esc(name)}" title="Load">▶</button>
                    <button class="btn btn-default btn-icon" data-default="${esc(name)}" title="${name === def ? 'Clear boot default' : 'Load at boot'}">${name === def ? '★' : '☆'}</button>
                    <button class="btn btn-danger btn-icon" data-del="${esc(name)}" title="Delete">🗑</button>
                </div>
            </div>`).join('') : '<div class="muted">No presets yet.</div>';
        const list = $('preset-list');
        list.querySelectorAll('[data-load]').forEach((b) => (b.onclick = () => run(() => apiClient.loadPreset(b.dataset.load))));
        list.querySelectorAll('[data-default]').forEach((b) => (b.onclick = () => run(async () => {
            await (b.dataset.default === def ? apiClient.clearDefaultPreset() : apiClient.setDefaultPreset(b.dataset.default));
            await loadPresets();
        })));
        list.querySelectorAll('[data-del]').forEach((b) => (b.onclick = () => {
            if (confirm(`Delete preset "${b.dataset.del}"?`)) run(async () => { await apiClient.deletePreset(b.dataset.del); await loadPresets(); });
        }));
    } catch (e) { toast(e.message); }
}

async function loadFiles() {
    try { files = await apiClient.listFiles(); } catch (e) { toast(e.message); files = []; }
    const fmt = (b) => (b > 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB');
    $('file-list').innerHTML = files.length ? files.map((f) => `
        <div class="file-item">
            <div class="file-info"><div class="file-name">${esc(f.name)}</div><div class="file-size">${fmt(f.size)}</div></div>
            <div class="file-actions">
                <button class="btn btn-primary btn-icon" data-use="${esc(f.name)}" title="Use in selected tile">↪</button>
                <button class="btn btn-danger btn-icon" data-fdel="${esc(f.name)}" title="Delete">🗑</button>
            </div>
        </div>`).join('') : '<div class="muted">No files uploaded.</div>';
    $('file-list').querySelectorAll('[data-use]').forEach((b) => (b.onclick = () => {
        if (sel() === null) return toast('Select a tile first');
        run(() => apiClient.playZone(sel(), { source: b.dataset.use }));
    }));
    $('file-list').querySelectorAll('[data-fdel]').forEach((b) => (b.onclick = () => {
        if (confirm(`Delete ${b.dataset.fdel}?`)) run(async () => { await apiClient.deleteFile(b.dataset.fdel); await loadFiles(); });
    }));
    renderDatalist();
}

async function handleUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    $('upload-progress').style.display = 'block';
    try {
        await apiClient.uploadFile(file, (p) => {
            $('upload-progress-bar').style.width = p + '%';
            $('upload-progress-text').textContent = Math.round(p) + '%';
        });
        toast(`Uploaded ${file.name}`, false);
        await loadFiles();
    } catch (err) { toast(err.message); }
    $('upload-progress').style.display = 'none';
    e.target.value = '';
}
