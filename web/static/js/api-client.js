/**
 * API Client - HTTP requests to the multiviewer API
 */
class APIClient {
    constructor(baseUrl = '') { this.baseUrl = baseUrl; }

    // global
    getStatus()                 { return this.get('/api/status'); }
    getTemplates()              { return this.get('/api/templates'); }
    getTestSources()            { return this.get('/api/test-sources'); }
    setDisplayResolution(w, h)  { return this.post('/api/display/resolution', { width: w, height: h }); }
    playAll()                   { return this.post('/api/play-all'); }
    stopAll()                   { return this.post('/api/stop-all'); }
    audioFollow(zone)           { return this.post('/api/audio', { zone }); }
    applyTemplate(template, gap){ return this.post('/api/layout/template', { template, gap }); }
    applyGrid(cols, rows, gap)  { return this.post('/api/layout/template', { cols, rows, gap }); }

    // zones
    addZone(cfg = {})           { return this.post('/api/zones', cfg); }
    updateZone(id, data)        { return this.request('PATCH', `/api/zones/${id}`, data); }
    deleteZone(id)              { return this.request('DELETE', `/api/zones/${id}`); }
    playZone(id, data = {})     { return this.post(`/api/zones/${id}/play`, data); }
    stopZone(id)                { return this.post(`/api/zones/${id}/stop`); }
    pauseZone(id)               { return this.post(`/api/zones/${id}/pause`); }
    setVolume(id, volume)       { return this.post(`/api/zones/${id}/volume`, { volume }); }
    setTally(id, state)         { return this.post(`/api/zones/${id}/tally`, { state }); }
    moveLayer(id, direction)    { return this.post(`/api/zones/${id}/layer`, { direction }); }

    // presets
    listPresets()               { return this.get('/api/presets'); }
    savePreset(name, description = '') { return this.post('/api/presets', { name, description }); }
    loadPreset(name)            { return this.post(`/api/presets/${encodeURIComponent(name)}/load`); }
    deletePreset(name)          { return this.request('DELETE', `/api/presets/${encodeURIComponent(name)}`); }
    setDefaultPreset(name)      { return this.post(`/api/presets/${encodeURIComponent(name)}/set-default`); }
    clearDefaultPreset()        { return this.post('/api/presets/clear-default'); }

    // files
    listFiles()                 { return this.get('/api/files'); }
    deleteFile(name)            { return this.request('DELETE', `/api/files/${encodeURIComponent(name)}`); }

    uploadFile(file, onProgress = null) {
        const formData = new FormData();
        formData.append('file', file);
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            if (onProgress) {
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable) onProgress((e.loaded / e.total) * 100);
                });
            }
            xhr.addEventListener('load', () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({ status: 'ok' }); }
                } else reject(new Error(`Upload failed: ${xhr.status}`));
            });
            xhr.addEventListener('error', () => reject(new Error('Upload failed')));
            xhr.open('POST', `${this.baseUrl}/api/upload`);
            xhr.send(formData);
        });
    }

    // helpers
    get(endpoint)        { return this.request('GET', endpoint); }
    post(endpoint, data) { return this.request('POST', endpoint, data || {}); }

    async request(method, endpoint, data) {
        const opts = { method, headers: {} };
        if (data !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(data);
        }
        const res = await fetch(`${this.baseUrl}${endpoint}`, opts);
        let json = null;
        try { json = await res.json(); } catch { /* empty body */ }
        if (!res.ok) throw new Error((json && json.error) || `HTTP ${res.status}`);
        return json;
    }
}

window.apiClient = new APIClient();
