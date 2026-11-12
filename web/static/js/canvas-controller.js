/**
 * Canvas Controller - multi-zone drag & resize editor
 * Zones are drawn in stacking order (last = on top). Hit testing goes top-down.
 */
const ZONE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#a855f7', '#ec4899', '#06b6d4',
                     '#84cc16', '#f97316', '#6366f1', '#14b8a6', '#eab308', '#ef4444'];
const TALLY_STROKE = { program: '#ef4444', preview: '#22c55e', standby: '#f59e0b' };

class CanvasController {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.displayWidth = 1920;
        this.displayHeight = 1080;
        this.zones = [];
        this.selectedId = null;
        this.snap = 8;
        this.handleSize = 14;      // canvas px
        this.minSize = 64;         // display px

        this.mode = null;          // null | 'move' | 'tl' | 'tr' | 'bl' | 'br'
        this.dragStart = null;
        this.zoneStart = null;

        this.onSelect = () => {};
        this.onChange = () => {};  // live, while dragging
        this.onCommit = () => {};  // on mouse release

        this.canvas.addEventListener('pointerdown', (e) => this.down(e));
        window.addEventListener('pointermove', (e) => this.move(e));
        window.addEventListener('pointerup', (e) => this.up(e));
        this.canvas.addEventListener('pointermove', (e) => this.hover(e));
        this.setDisplayResolution(1920, 1080);
    }

    get busy() { return this.mode !== null; }
    get scale() { return this.canvas.width / this.displayWidth; }

    setDisplayResolution(w, h) {
        this.displayWidth = w;
        this.displayHeight = h;
        this.canvas.height = Math.round(this.canvas.width * h / w);
        this.render();
    }

    setZones(zones) {
        if (this.busy) return;      // don't fight the user while dragging
        this.zones = zones.map((z) => ({ ...z, geometry: { ...z.geometry } }));
        if (this.selectedId !== null && !this.zones.find((z) => z.id === this.selectedId)) {
            this.selectedId = null;
            this.onSelect(null);
        }
        this.render();
    }

    select(id) {
        this.selectedId = id;
        this.onSelect(id === null ? null : this.zones.find((z) => z.id === id));
        this.render();
    }

    colorFor(id) { return ZONE_COLORS[(id - 1) % ZONE_COLORS.length]; }

    pos(e) {
        const r = this.canvas.getBoundingClientRect();
        return {
            x: (e.clientX - r.left) * (this.canvas.width / r.width) / this.scale,
            y: (e.clientY - r.top) * (this.canvas.height / r.height) / this.scale,
        };
    }

    hitHandle(z, p) {
        const g = z.geometry;
        const rad = (this.handleSize * 1.2) / this.scale;
        const corners = { tl: [g.x, g.y], tr: [g.x + g.width, g.y],
                          bl: [g.x, g.y + g.height], br: [g.x + g.width, g.y + g.height] };
        for (const [k, [cx, cy]] of Object.entries(corners)) {
            if (Math.hypot(p.x - cx, p.y - cy) <= rad) return k;
        }
        return null;
    }

    hitZone(p) {
        for (let i = this.zones.length - 1; i >= 0; i--) {
            const g = this.zones[i].geometry;
            if (p.x >= g.x && p.x <= g.x + g.width && p.y >= g.y && p.y <= g.y + g.height) return this.zones[i];
        }
        return null;
    }

    selected() { return this.zones.find((z) => z.id === this.selectedId); }

    down(e) {
        const p = this.pos(e);
        const sel = this.selected();
        const handle = sel && this.hitHandle(sel, p);
        if (handle) {
            this.mode = handle;
        } else {
            const z = this.hitZone(p);
            if (!z) { this.select(null); return; }
            if (z.id !== this.selectedId) this.select(z.id);
            this.mode = 'move';
        }
        this.dragStart = p;
        this.zoneStart = { ...this.selected().geometry };
        this.canvas.setPointerCapture?.(e.pointerId);
        e.preventDefault();
    }

    snapV(v) { return this.snap > 0 ? Math.round(v / this.snap) * this.snap : Math.round(v); }

    move(e) {
        if (!this.mode) return;
        const p = this.pos(e);
        const dx = p.x - this.dragStart.x, dy = p.y - this.dragStart.y;
        const s = this.zoneStart, W = this.displayWidth, H = this.displayHeight;
        let { x, y, width, height } = s;

        if (this.mode === 'move') {
            x = this.snapV(Math.max(0, Math.min(W - width, s.x + dx)));
            y = this.snapV(Math.max(0, Math.min(H - height, s.y + dy)));
            // snapping must not push the tile off-screen
            x = Math.min(x, W - width); y = Math.min(y, H - height);
        } else {
            let left = s.x, top = s.y, right = s.x + s.width, bottom = s.y + s.height;
            if (this.mode.includes('l')) left = this.snapV(Math.max(0, Math.min(right - this.minSize, s.x + dx)));
            if (this.mode.includes('r')) right = this.snapV(Math.min(W, Math.max(left + this.minSize, right + dx)));
            if (this.mode.includes('t')) top = this.snapV(Math.max(0, Math.min(bottom - this.minSize, s.y + dy)));
            if (this.mode.includes('b')) bottom = this.snapV(Math.min(H, Math.max(top + this.minSize, bottom + dy)));
            x = left; y = top; width = Math.min(W, right) - left; height = Math.min(H, bottom) - top;
        }
        const z = this.selected();
        z.geometry = { x, y, width, height };
        this.render();
        this.onChange(z);
    }

    up() {
        if (!this.mode) return;
        const z = this.selected();
        const changed = z && JSON.stringify(z.geometry) !== JSON.stringify(this.zoneStart);
        this.mode = null;
        if (changed) this.onCommit(z);
    }

    hover(e) {
        if (this.mode) return;
        const p = this.pos(e);
        const sel = this.selected();
        const h = sel && this.hitHandle(sel, p);
        const cursors = { tl: 'nwse-resize', br: 'nwse-resize', tr: 'nesw-resize', bl: 'nesw-resize' };
        this.canvas.style.cursor = h ? cursors[h] : (this.hitZone(p) ? 'move' : 'default');
    }

    // ---------------------------------------------------------------- render
    render() {
        const ctx = this.ctx, s = this.scale;
        ctx.fillStyle = '#111';
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

        // grid every 10% for orientation
        ctx.strokeStyle = '#262626';
        ctx.lineWidth = 1;
        for (let i = 1; i < 10; i++) {
            const x = Math.round(this.canvas.width * i / 10) + 0.5;
            const y = Math.round(this.canvas.height * i / 10) + 0.5;
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, this.canvas.height); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(this.canvas.width, y); ctx.stroke();
        }

        ctx.fillStyle = '#666';
        ctx.font = '12px monospace';
        ctx.fillText(`${this.displayWidth}×${this.displayHeight}  •  ${this.zones.length} tiles`, 8, this.canvas.height - 8);

        for (const z of this.zones) this.drawZone(z, s);
    }

    drawZone(z, s) {
        const ctx = this.ctx, g = z.geometry;
        const x = g.x * s, y = g.y * s, w = g.width * s, h = g.height * s;
        const color = this.colorFor(z.id);
        const selected = z.id === this.selectedId;

        ctx.fillStyle = '#000';
        ctx.fillRect(x, y, w, h);
        ctx.fillStyle = color + (z.status === 'playing' ? '55' : '22');
        ctx.fillRect(x, y, w, h);

        const tally = TALLY_STROKE[z.tally];
        ctx.lineWidth = tally ? 4 : (selected ? 3 : 1.5);
        ctx.strokeStyle = tally || (selected ? '#fff' : color);
        ctx.strokeRect(x + ctx.lineWidth / 2, y + ctx.lineWidth / 2, w - ctx.lineWidth, h - ctx.lineWidth);
        if (tally && selected) {
            ctx.setLineDash([6, 4]); ctx.lineWidth = 1.5; ctx.strokeStyle = '#fff';
            ctx.strokeRect(x + 6, y + 6, w - 12, h - 12); ctx.setLineDash([]);
        }

        ctx.save();
        ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
        const fs = Math.max(11, Math.min(22, h / 7));
        ctx.font = `bold ${fs}px sans-serif`;
        ctx.fillStyle = '#fff';
        ctx.fillText(`${z.id} · ${z.name || ''}`, x + 8, y + fs + 4);

        ctx.font = `${Math.max(10, fs * 0.7)}px monospace`;
        ctx.fillStyle = '#ddd';
        const src = z.source ? z.source.replace(/^(\w+:\/\/)([^@/]*@)?/, '$1') : '— no source —';
        ctx.fillText(src.length > 48 ? src.slice(0, 45) + '…' : src, x + 8, y + fs * 1.9 + 6);
        ctx.fillText(`${g.width}×${g.height} @ ${g.x},${g.y}`, x + 8, y + fs * 2.7 + 8);

        // status dot + audio icon
        const dot = { playing: '#10b981', paused: '#f59e0b', starting: '#3b82f6', error: '#ef4444' }[z.status] || '#6b7280';
        ctx.fillStyle = z.health === 'reconnecting' || z.health === 'lost' ? '#ef4444' : dot;
        ctx.beginPath(); ctx.arc(x + w - 14, y + 14, 6, 0, Math.PI * 2); ctx.fill();
        if (z.audio) { ctx.font = '14px sans-serif'; ctx.fillText('🔊', x + w - 42, y + 20); }
        if (z.health === 'reconnecting' || z.health === 'lost') {
            ctx.fillStyle = '#ef4444'; ctx.font = `bold ${fs}px sans-serif`; ctx.textAlign = 'center';
            ctx.fillText(z.health === 'lost' ? 'NO SIGNAL' : 'RECONNECTING', x + w / 2, y + h / 2);
            ctx.textAlign = 'left';
        }
        ctx.restore();

        if (selected) {
            for (const [hx, hy] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) {
                ctx.fillStyle = '#fff';
                ctx.beginPath(); ctx.arc(hx, hy, this.handleSize / 2, 0, Math.PI * 2); ctx.fill();
                ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
            }
        }
    }
}
