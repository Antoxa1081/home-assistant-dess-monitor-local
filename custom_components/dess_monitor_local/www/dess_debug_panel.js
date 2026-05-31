// DESS Monitor Local — debug panel (vanilla custom element, no build step).
// HA sets `.hass` on this element. We pull a state snapshot over the HA
// WebSocket and subscribe to the live diag_hub event stream.
//
// WS commands (see debug_panel.py):
//   dess_monitor_local/diag/state      -> {hubs:[{dongles, coordinator}], events}
//   dess_monitor_local/diag/subscribe  -> live {t: frame|session|cycle|dongles}
//   dess_monitor_local/diag/send_frame -> {result}

const MAX_EVENTS = 600;

class DessDebugPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._subscribed = false;
    this._unsub = null;
    this._events = [];
    this._state = { hubs: [] };
    this._filter = "";
    this._paused = false;
    this._pollTimer = null;
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
        this._events = s.events.slice(-MAX_EVENTS);
      }
      this._renderDongles();
      this._renderHeader();
    } catch (e) { /* transient — next tick retries */ }
  }

  _onEvent(ev) {
    if (this._paused) return;
    this._events.push(ev);
    if (this._events.length > MAX_EVENTS) this._events.splice(0, this._events.length - MAX_EVENTS);
    this._renderEvents();
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
        .ok { color:#4caf50; } .bad { color:#e57373; } .muted { color:#888; }
        .grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; min-height:0; flex:1; }
        .card { background:#181818; border:1px solid #2a2a2a; border-radius:8px; padding:8px; display:flex; flex-direction:column; min-height:0; }
        .card h3 { margin:0 0 6px; font-size:13px; color:#9aa; }
        .log { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:11px; overflow:auto; flex:1; line-height:1.5; }
        .log .frame { color:#9ad; } .log .session { color:#fc8; } .log .cycle { color:#8c8; }
        .log .ts { color:#666; margin-right:6px; }
        .ctrl { display:flex; gap:8px; align-items:center; }
        input,button { background:#222; color:inherit; border:1px solid #333; border-radius:6px; padding:4px 8px; font-size:12px; }
        button { cursor:pointer; }
      </style>
      <div class="wrap">
        <div class="hdr">
          <h2>DESS Debug</h2>
          <span id="hdrstats" class="pill">connecting…</span>
          <span class="ctrl">
            <input id="filter" placeholder="filter events (pn / cmd / text)" size="28"/>
            <button id="pause">Pause</button>
            <button id="clear">Clear</button>
          </span>
        </div>
        <table><thead><tr>
          <th>PN</th><th>Name</th><th>Status</th><th>Proto</th><th>Addr</th><th>Peer</th><th>Last seen</th><th>On</th>
        </tr></thead><tbody id="dongles"></tbody></table>
        <div class="grid">
          <div class="card"><h3>Event stream</h3><div id="log" class="log"></div></div>
          <div class="card"><h3>Coordinator</h3><div id="coord" class="log"></div></div>
        </div>
      </div>`;
    const f = this.shadowRoot.getElementById("filter");
    f.addEventListener("input", () => { this._filter = f.value.toLowerCase(); this._renderEvents(); });
    this.shadowRoot.getElementById("pause").addEventListener("click", (e) => {
      this._paused = !this._paused; e.target.textContent = this._paused ? "Resume" : "Pause";
    });
    this.shadowRoot.getElementById("clear").addEventListener("click", () => {
      this._events = []; this._renderEvents();
    });
  }

  _renderHeader() {
    const el = this.shadowRoot.getElementById("hdrstats");
    if (!el) return;
    const hub = (this._state.hubs || [])[0];
    const dongles = hub ? hub.dongles.length : 0;
    const conn = hub ? hub.dongles.filter((d) => d.status === "connected").length : 0;
    el.textContent = `${this._state.hubs.length} hub · ${conn}/${dongles} dongles connected`;
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
        rows.push(`<tr>
          <td>${d.pn}</td><td>${d.name || ""}</td>
          <td class="${cls}">${d.status}</td>
          <td>${d.protocol || "<span class='muted'>none</span>"}</td>
          <td>${d.devaddr}</td><td class="muted">${d.peer || "—"}</td>
          <td class="muted">${this._ago(d.last_seen)}</td>
          <td>${d.enabled ? "✓" : ""}</td>
        </tr>`);
      }
    }
    tb.innerHTML = rows.join("") || `<tr><td colspan="8" class="muted">no dongles discovered yet</td></tr>`;
    this._renderCoord();
  }

  _renderCoord() {
    const el = this.shadowRoot.getElementById("coord");
    if (!el) return;
    const hub = (this._state.hubs || [])[0];
    const c = hub && hub.coordinator;
    if (!c || !c.present) { el.textContent = "no coordinator"; return; }
    const fails = Object.entries(c.consecutive_failures || {}).filter(([, n]) => n > 0);
    el.innerHTML =
      `interval: ${c.update_interval_seconds}s · last ok: ${c.last_update_success}<br>` +
      `children: ${(c.devices || []).map((d) => d.id).join(", ")}<br>` +
      (fails.length ? `<span class="bad">failing: ${fails.map(([k, n]) => `${k}=${n}`).join(", ")}</span>` : `<span class="ok">no failures</span>`);
  }

  _renderEvents() {
    const el = this.shadowRoot.getElementById("log");
    if (!el) return;
    const f = this._filter;
    const out = [];
    for (const e of this._events) {
      const line = this._fmt(e);
      if (f && !line.toLowerCase().includes(f)) continue;
      out.push(`<div class="${e.t}"><span class="ts">${this._clock(e.ts)}</span>${line}</div>`);
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
    if (e.t === "session") return `SESSION ${e.ev} pn=${e.pn || "?"} peer=${e.peer || ""}${e.age_s != null ? ` age=${e.age_s}s` : ""}`;
    if (e.t === "cycle") {
      const ch = e.children ? Object.entries(e.children).map(([k, v]) => `${k.split(":").slice(1).join(":")}=${v}`).join(" ") : "";
      return `CYCLE dur=${e.dur_s}s ${ch}`;
    }
    if (e.t === "frame") return `${(e.dir || "").toUpperCase()} pn=${e.pn || "?"} fc=${e.fc} addr=${e.devaddr}${e.cmd ? ` ${e.cmd}` : ""} ${e.hex || ""}`;
    return JSON.stringify(e);
  }
}

customElements.define("dess-debug-panel", DessDebugPanel);
