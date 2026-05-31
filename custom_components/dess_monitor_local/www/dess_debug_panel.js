// DESS Monitor Local — debug panel (vanilla custom element, no build step).
// HA sets `.hass` on this element. We pull a state snapshot over the HA
// WebSocket and subscribe to the live diag_hub event stream.
//
// WS commands (see debug_panel.py):
//   dess_monitor_local/diag/state      -> {hubs:[{dongles, coordinator}], events}
//   dess_monitor_local/diag/subscribe  -> live {t: frame|session|cycle}
//   dess_monitor_local/diag/send_frame -> {result}

const MAX_EVENTS = 800;
const MAX_CYCLES = 60;
const LAT_WINDOW = 30; // rolling latency samples per dongle

function hexToAscii(hex) {
  if (!hex) return "";
  let s = "";
  for (let i = 0; i + 1 < hex.length; i += 2) {
    const c = parseInt(hex.substr(i, 2), 16);
    s += c >= 0x20 && c < 0x7f ? String.fromCharCode(c) : ".";
  }
  return s;
}

class DessDebugPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._subscribed = false;
    this._unsub = null;
    this._events = [];
    this._cycles = [];
    this._state = { hubs: [] };
    this._filter = "";
    this._paused = false;
    this._pollTimer = null;
    this._pending = new Map(); // `${pn}:${tid}` -> tx ts (latency matching)
    this._lat = new Map(); // pn -> [ms,...] rolling
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._subscribed && hass && hass.connection) {
      this._subscribed = true;
      this._init();
    }
  }
  get hass() { return this._hass; }

  connectedCallback() { this._renderShell(); }
  disconnectedCallback() {
    if (this._unsub) this._unsub.then((u) => u && u()).catch(() => {});
    if (this._pollTimer) clearInterval(this._pollTimer);
  }

  async _init() {
    await this._refreshState();
    this._unsub = this._hass.connection.subscribeMessage(
      (ev) => this._onEvent(ev),
      { type: "dess_monitor_local/diag/subscribe" }
    );
    this._pollTimer = setInterval(() => this._refreshState(), 2000);
  }

  async _refreshState() {
    try {
      const s = await this._hass.connection.sendMessagePromise({
        type: "dess_monitor_local/diag/state",
      });
      this._state = s || { hubs: [] };
      if (s && s.events && this._events.length === 0) {
        for (const e of s.events.slice(-MAX_EVENTS)) this._ingest(e);
      }
      this._renderDongles();
      this._renderHeader();
      this._renderCoord();
      this._fillDeviceSelect();
    } catch (e) { /* transient — retried next tick */ }
  }

  // Latency matching + cycle bookkeeping for every event (live OR replayed).
  _ingest(ev) {
    if (ev.t === "frame") {
      const k = `${ev.pn}:${ev.tid}`;
      if (ev.dir === "tx") this._pending.set(k, ev.ts);
      else if (ev.dir === "rx" && ev.fc === 4 && this._pending.has(k)) {
        const ms = Math.round((ev.ts - this._pending.get(k)) * 1000);
        this._pending.delete(k);
        ev._lat = ms;
        const arr = this._lat.get(ev.pn) || [];
        arr.push(ms);
        if (arr.length > LAT_WINDOW) arr.shift();
        this._lat.set(ev.pn, arr);
      }
    } else if (ev.t === "cycle") {
      this._cycles.push(ev);
      if (this._cycles.length > MAX_CYCLES) this._cycles.shift();
    }
    this._events.push(ev);
    if (this._events.length > MAX_EVENTS) this._events.splice(0, this._events.length - MAX_EVENTS);
  }

  _onEvent(ev) {
    if (this._paused) return;
    this._ingest(ev);
    this._renderEvents();
    if (ev.t === "cycle") this._renderCycles();
  }

  _avgLat(pn) {
    const a = this._lat.get(pn);
    if (!a || !a.length) return null;
    return Math.round(a.reduce((x, y) => x + y, 0) / a.length);
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; font-family:system-ui,sans-serif; color:var(--primary-text-color,#e1e1e1);
          background:var(--primary-background-color,#111); height:100%; box-sizing:border-box; }
        .wrap { padding:12px; height:100%; box-sizing:border-box; display:flex; flex-direction:column; gap:10px; }
        h2 { margin:0; font-size:16px; }
        .hdr { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
        .pill { font-size:12px; padding:2px 8px; border-radius:10px; background:#2a2a2a; }
        table { border-collapse:collapse; width:100%; font-size:12px; }
        th,td { text-align:left; padding:4px 8px; border-bottom:1px solid #2a2a2a; white-space:nowrap; }
        th { color:#9aa; font-weight:600; }
        .ok { color:#4caf50; } .bad { color:#e57373; } .warn { color:#ffb74d; } .muted { color:#888; }
        .grid { display:grid; grid-template-columns:1.4fr 1fr; gap:10px; min-height:0; flex:1; }
        .card { background:#181818; border:1px solid #2a2a2a; border-radius:8px; padding:8px; display:flex; flex-direction:column; min-height:0; }
        .card h3 { margin:0 0 6px; font-size:13px; color:#9aa; display:flex; justify-content:space-between; }
        .log { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:11px; overflow:auto; flex:1; line-height:1.55; }
        .log .tx { color:#9ad; } .log .rx { color:#8c8; } .log .session { color:#fc8; } .log .cycle { color:#bb9; }
        .log .ts { color:#666; margin-right:6px; } .log .lat { color:#6cf; } .log .asc { color:#ddd; }
        .ctrl { display:flex; gap:6px; align-items:center; }
        input,button,select { background:#222; color:inherit; border:1px solid #333; border-radius:6px; padding:4px 8px; font-size:12px; }
        button { cursor:pointer; } button:hover { background:#2c2c2c; }
        .spark { display:flex; align-items:flex-end; gap:2px; height:34px; margin:4px 0; }
        .spark span { width:6px; background:#3a6; border-radius:1px 1px 0 0; }
        .spark span.slow { background:#b85; } .spark span.cap { background:#c55; }
        .right { display:flex; flex-direction:column; gap:10px; min-height:0; }
        .cyc { font-family:ui-monospace,monospace; font-size:11px; overflow:auto; flex:1; }
        pre { margin:6px 0 0; white-space:pre-wrap; word-break:break-all; font-size:11px; color:#9d9; }
      </style>
      <div class="wrap">
        <div class="hdr">
          <h2>DESS Debug</h2>
          <span id="hdrstats" class="pill">connecting…</span>
          <span class="ctrl">
            <input id="filter" placeholder="filter (pn / cmd / hex / text)" size="26"/>
            <button id="pause">Pause</button>
            <button id="clear">Clear</button>
          </span>
        </div>
        <table><thead><tr>
          <th>PN</th><th>Name</th><th>Status</th><th>Proto</th><th>Addr</th><th>Peer</th><th>Last seen</th><th>Latency</th><th>On</th>
        </tr></thead><tbody id="dongles"></tbody></table>
        <div class="grid">
          <div class="card"><h3><span>Event stream</span><span id="evcount" class="muted"></span></h3><div id="log" class="log"></div></div>
          <div class="right">
            <div class="card" style="flex:0 0 auto">
              <h3>Cycles <span id="coordsum" class="muted"></span></h3>
              <div id="spark" class="spark"></div>
              <div id="cyc" class="cyc" style="max-height:110px"></div>
            </div>
            <div class="card" style="flex:1 1 auto">
              <h3>Send frame</h3>
              <div class="ctrl">
                <select id="dev"></select>
                <input id="cmd" value="QPIGS" size="10"/>
                <button id="send">Send</button>
              </div>
              <pre id="sendout" class="muted">—</pre>
            </div>
          </div>
        </div>
      </div>`;
    const f = this.shadowRoot.getElementById("filter");
    f.addEventListener("input", () => { this._filter = f.value.toLowerCase(); this._renderEvents(); });
    this.shadowRoot.getElementById("pause").addEventListener("click", (e) => {
      this._paused = !this._paused; e.target.textContent = this._paused ? "Resume" : "Pause";
    });
    this.shadowRoot.getElementById("clear").addEventListener("click", () => {
      this._events = []; this._cycles = []; this._renderEvents(); this._renderCycles();
    });
    this.shadowRoot.getElementById("send").addEventListener("click", () => this._send());
  }

  _renderHeader() {
    const el = this.shadowRoot.getElementById("hdrstats");
    if (!el) return;
    const hub = (this._state.hubs || [])[0];
    const dongles = hub ? hub.dongles.length : 0;
    const conn = hub ? hub.dongles.filter((d) => d.status === "connected").length : 0;
    const lastCyc = this._cycles[this._cycles.length - 1];
    el.textContent = `${this._state.hubs.length} hub · ${conn}/${dongles} connected`
      + (lastCyc ? ` · last cycle ${lastCyc.dur_s}s` : "");
  }

  _ago(iso) {
    if (!iso) return "—";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    return s < 60 ? `${s.toFixed(0)}s` : `${(s / 60).toFixed(1)}m`;
  }

  _renderDongles() {
    const tb = this.shadowRoot.getElementById("dongles");
    if (!tb) return;
    const rows = [];
    for (const hub of this._state.hubs || []) {
      for (const d of hub.dongles) {
        const cls = d.status === "connected" ? "ok" : "bad";
        const lat = this._avgLat(d.pn);
        rows.push(`<tr>
          <td>${d.pn}</td><td>${d.name || ""}</td>
          <td class="${cls}">${d.status}</td>
          <td>${d.protocol || "<span class='muted'>none</span>"}</td>
          <td>${d.devaddr}</td><td class="muted">${d.peer || "—"}</td>
          <td class="muted">${this._ago(d.last_seen)}</td>
          <td class="lat">${lat != null ? lat + "ms" : "—"}</td>
          <td>${d.enabled ? "✓" : ""}</td>
        </tr>`);
      }
    }
    tb.innerHTML = rows.join("") || `<tr><td colspan="9" class="muted">no dongles discovered yet</td></tr>`;
  }

  _renderCoord() {
    const el = this.shadowRoot.getElementById("coordsum");
    if (!el) return;
    const c = ((this._state.hubs || [])[0] || {}).coordinator;
    if (!c || !c.present) { el.textContent = ""; return; }
    const fails = Object.entries(c.consecutive_failures || {}).filter(([, n]) => n > 0);
    el.innerHTML = `interval ${c.update_interval_seconds}s · `
      + (fails.length ? `<span class="bad">${fails.length} failing</span>` : `<span class="ok">healthy</span>`);
  }

  _renderCycles() {
    const sp = this.shadowRoot.getElementById("spark");
    const cyc = this.shadowRoot.getElementById("cyc");
    if (!sp || !cyc) return;
    const max = Math.max(5, ...this._cycles.map((c) => c.dur_s));
    sp.innerHTML = this._cycles.map((c) => {
      const h = Math.max(2, Math.round((c.dur_s / max) * 32));
      const cls = c.dur_s >= 24 ? "cap" : c.dur_s >= 12 ? "slow" : "";
      return `<span class="${cls}" style="height:${h}px" title="cycle ${c.n}: ${c.dur_s}s"></span>`;
    }).join("");
    cyc.innerHTML = this._cycles.slice(-12).reverse().map((c) => {
      const ch = Object.entries(c.children || {}).map(([k, v]) => {
        const id = k.split(":").slice(1).join(":") || k;
        return `<span class="${v === "ok" ? "ok" : "bad"}">${id}=${v}</span>`;
      }).join(" ");
      return `<div><span class="ts">#${c.n}</span> ${c.dur_s}s &nbsp; ${ch}</div>`;
    }).join("");
  }

  _renderEvents() {
    const el = this.shadowRoot.getElementById("log");
    const cnt = this.shadowRoot.getElementById("evcount");
    if (!el) return;
    if (cnt) cnt.textContent = `${this._events.length} ev`;
    const f = this._filter;
    const out = [];
    for (const e of this._events) {
      const line = this._fmt(e);
      const cls = e.t === "frame" ? e.dir : e.t;
      if (f && !(line + " " + (e.hex || "")).toLowerCase().includes(f)) continue;
      out.push(`<div class="${cls}"><span class="ts">${this._clock(e.ts)}</span>${line}</div>`);
    }
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    el.innerHTML = out.slice(-MAX_EVENTS).join("");
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  _clock(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
  }

  _fmt(e) {
    if (e.t === "session") return `SESSION ${e.ev} pn=${e.pn || "?"} ${e.peer || ""}${e.age_s != null ? ` age=${e.age_s}s` : ""}`;
    if (e.t === "cycle") return `CYCLE #${e.n} dur=${e.dur_s}s`;
    if (e.t === "frame") {
      const asc = hexToAscii(e.hex).trim();
      const lat = e._lat != null ? ` <span class="lat">${e._lat}ms</span>` : "";
      return `${e.dir.toUpperCase()} pn=${e.pn || "?"} fc=${e.fc} addr=${e.devaddr}`
        + `${e.cmd ? ` ${e.cmd}` : ""}${lat} <span class="asc">${asc || e.hex}</span>`;
    }
    return JSON.stringify(e);
  }

  _fillDeviceSelect() {
    const sel = this.shadowRoot.getElementById("dev");
    if (!sel) return;
    const devs = [];
    for (const hub of this._state.hubs || [])
      for (const d of (hub.coordinator && hub.coordinator.devices) || []) devs.push(d);
    if (sel.options.length === devs.length && sel.options.length) return; // unchanged
    const cur = sel.value;
    sel.innerHTML = devs.map((d) => `<option value="${d.uri}">${d.name || d.id}</option>`).join("")
      || `<option value="">no devices</option>`;
    if (cur) sel.value = cur;
  }

  async _send() {
    const out = this.shadowRoot.getElementById("sendout");
    const device = this.shadowRoot.getElementById("dev").value;
    const command = this.shadowRoot.getElementById("cmd").value.trim();
    if (!device || !command) { out.textContent = "pick a device + command"; return; }
    out.textContent = `→ ${command} …`;
    try {
      const r = await this._hass.connection.sendMessagePromise({
        type: "dess_monitor_local/diag/send_frame", device, command,
      });
      out.textContent = JSON.stringify(r.result, null, 2);
    } catch (err) {
      out.textContent = "error: " + (err && err.message ? err.message : JSON.stringify(err));
    }
  }
}

customElements.define("dess-debug-panel", DessDebugPanel);
