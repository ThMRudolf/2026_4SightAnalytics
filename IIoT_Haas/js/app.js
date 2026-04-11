/**
 * app.js — CNC Fräsmaschinen-Dashboard
 * =====================================
 *
 * CORS-PROBLEM UND LÖSUNG
 * -----------------------
 * Der Haas NGC-Controller sendet keinen "Access-Control-Allow-Origin"-Header.
 * Jeder Browser blockiert deshalb direkte fetch()-Aufrufe an den Agent.
 *
 * Diese Datei löst das Problem durch konsequentes Proxy-Routing:
 *
 *   Option A — cors_proxy.py (empfohlen für standalone HTML-Dashboard):
 *     python cors_proxy.py --mtc-host 148.205.38.221 --mtc-port 8082
 *     → Trägt http://localhost:8888 als PROXY URL im Modal ein
 *     → Browser ruft localhost:8888/proxy/current ab (kein CORS)
 *     → Proxy leitet server-seitig an den Agent weiter
 *
 *   Option B — Python-Backend (Docker-Stack):
 *     docker compose up
 *     → Trägt http://localhost:8080 als BACKEND URL ein
 *     → Browser ruft localhost:8080/api/latest ab (JSON aus SQLite)
 *     → Backend pollt den Agent intern
 *
 *   Option C — Direkt (NUR wenn Agent CORS-Header sendet):
 *     → Nur IP eingeben, kein Proxy, kein Backend
 *     → Wird für Haas-Controller NICHT funktionieren
 *
 * DATEN-PRIORITÄT IM POLL-LOOP
 * ----------------------------
 *   1. BACKEND_URL gesetzt  →  /api/latest (JSON, DB-Verlauf)
 *   2. PROXY_URL gesetzt    →  /proxy/current + /proxy/sample (XML über Proxy)
 *   3. BASE_URL direkt      →  /current + /sample (XML, nur ohne CORS-Blockade)
 */

'use strict';

/* ==========================================================================
   KONFIGURATION
   ========================================================================== */

// Alle drei URLs starten als null und werden durch applyConfig() gesetzt.
// Immer nur die höchstpriorisierte gesetzte URL wird tatsächlich genutzt.

let BASE_URL        = null;  // MTConnect-Agent-IP, z.B. "http://148.205.38.221:8082"
let PROXY_URL       = null;  // CORS-Proxy,          z.B. "http://localhost:8888"
let BACKEND_URL     = null;  // Python-Backend,      z.B. "http://localhost:8080"
let SENSOR_ENDPOINT = null;  // Zukünftiger Sensor,  z.B. "http://192.168.1.101:7878/current"

const POLL_MS = 1000;  // Abfrageintervall: 1 Sekunde

/* ==========================================================================
   XML-HILFSFUNKTIONEN
   ========================================================================== */

const parseXML = text => new DOMParser().parseFromString(text, 'text/xml');

function xmlAll(doc, tag, name) {
  const out = [];
  const els = doc.getElementsByTagNameNS('*', tag);
  for (const e of els) {
    if (!name || e.getAttribute('name') === name)
      out.push({ value: e.textContent.trim(), ts: e.getAttribute('timestamp') });
  }
  return out;
}

const xmlGet = (doc, tag, name) => xmlAll(doc, tag, name)[0]?.value ?? null;

/* ==========================================================================
   ANWENDUNGSZUSTAND
   ========================================================================== */

const state = {
  spindleRPM: 0, xPos: 0, yPos: 0, zPos: 0,
  cycleTime: 0, spindleTime: 0, machineRunTime: 0,
  toolNum: 1, station: 10, spNum: 4, maxPwr: 7.0,
  ssovrd: '100%', fdovrd: '100%', rovrd: '100%',
  gcodes: [], program: '', m30c: 0, activeAlarms: '',
  velX: 0, velY: 0,
  posHistory: [],
  sensorSpindle: null, sensorX: null, sensorY: null,
  sensorHist: { s: [], x: [], y: [] },
};

/* ==========================================================================
   NETZWERK — EINZIGE FETCH-FUNKTION FÜR XML
   ==========================================================================
   Alle XML-Anfragen laufen durch diese Funktion.
   Sie wählt automatisch die richtige URL basierend auf der Konfiguration.
   BASE_URL wird NIE direkt für XML-Abrufe verwendet wenn Proxy/Backend gesetzt ist.
   ========================================================================== */

/**
 * Ruft einen MTConnect-XML-Pfad CORS-sicher ab.
 *
 * @param {string} mtcPath  - z.B. 'current', 'sample', 'probe'  (OHNE führenden /)
 * @returns {Document|null} - Geparster XML-DOM oder null bei Fehler
 */
async function fetchMTC(mtcPath) {
  let url;

  if (PROXY_URL) {
    // Lokaler CORS-Proxy (cors_proxy.py) — empfohlen
    url = `${PROXY_URL}/proxy/${mtcPath}`;
  } else if (BACKEND_URL) {
    // Python-Backend hat ebenfalls /proxy/<path>
    url = `${BACKEND_URL}/proxy/${mtcPath}`;
  } else {
    // Kein Proxy konfiguriert → Abbruch
    // BASE_URL wird NICHT direkt verwendet: der Haas-Controller sendet
    // kein CORS-Header, der Browser würde die Antwort blockieren.
    console.warn(`fetchMTC(${mtcPath}): kein Proxy konfiguriert. `
      + `Starte cors_proxy.py und trage http://localhost:8888 als PROXY URL ein.`);
    return null;
  }

  try {
    const r = await fetch(url, {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return parseXML(await r.text());
  } catch (e) {
    console.warn(`fetchMTC(${mtcPath}) → ${url}:`, e.message);
    return null;
  }
}

/* ==========================================================================
   NETZWERK — BACKEND-JSON-ABRUF
   ========================================================================== */

/**
 * Holt den vollständigen Maschinenzustand vom Python-Backend als JSON.
 * Wird nur aufgerufen wenn BACKEND_URL gesetzt ist.
 * @returns {boolean} true = Daten empfangen und state befüllt
 */
async function fetchFromBackend() {
  if (!BACKEND_URL) return false;
  try {
    const [latest, traj] = await Promise.all([
      fetch(`${BACKEND_URL}/api/latest`,          { cache: 'no-store', signal: AbortSignal.timeout(3000) }).then(r => r.json()),
      fetch(`${BACKEND_URL}/api/trajectory?limit=60`, { cache: 'no-store', signal: AbortSignal.timeout(3000) }).then(r => r.json()),
    ]);

    state.spindleRPM     = latest.spindle_rpm       ?? 0;
    state.cycleTime      = latest.cycle_time_s      ?? 0;
    state.spindleTime    = latest.spindle_time_s    ?? 0;
    state.machineRunTime = latest.machine_runtime_s ?? 0;
    state.ssovrd  = latest.spindle_override != null ? latest.spindle_override + '%' : '—';
    state.fdovrd  = latest.feed_override    != null ? latest.feed_override    + '%' : '—';
    state.rovrd   = latest.rapid_override   != null ? latest.rapid_override   + '%' : '—';
    state.gcodes  = latest.active_gcodes ? latest.active_gcodes.split(',').map(s => s.trim()).filter(Boolean) : [];
    state.program = latest.active_program ?? '';
    state.m30c    = latest.m30_counter    ?? '';
    state.activeAlarms = latest.active_alarms ?? '';

    const xAxis = latest.axes?.find(a => a.axis === 'X');
    const yAxis = latest.axes?.find(a => a.axis === 'Y');
    if (xAxis) { state.xPos = xAxis.position_mm; state.velX = xAxis.velocity_mm_s ?? 0; }
    if (yAxis) { state.yPos = yAxis.position_mm; state.velY = yAxis.velocity_mm_s ?? 0; }

    if (latest.tool) {
      state.toolNum = latest.tool.tool_number  ?? state.toolNum;
      state.station = latest.tool.station      ?? state.station;
      state.spNum   = latest.tool.spindle_num  ?? state.spNum;
      state.maxPwr  = latest.tool.max_power_kw ?? state.maxPwr;
    }

    if (latest.current_sensor) {
      const s = latest.current_sensor;
      state.sensorSpindle = s.spindle_a;
      state.sensorX = s.x_axis_a;
      state.sensorY = s.y_axis_a;
      const push = (arr, v) => { if (v != null) { arr.push(v); if (arr.length > 40) arr.shift(); } };
      push(state.sensorHist.s, state.sensorSpindle);
      push(state.sensorHist.x, state.sensorX);
      push(state.sensorHist.y, state.sensorY);
      ['ch-spindle','ch-x','ch-y'].forEach(id => document.getElementById(id)?.classList.add('live'));
      updateSensorUI();
    }

    if (Array.isArray(traj))
      state.posHistory = traj.map(r => ({ x: r.x ?? 0, y: r.y ?? 0, z: r.z ?? 0 }));

    return true;
  } catch (e) {
    console.warn('fetchFromBackend:', e.message);
    return false;
  }
}

/* ==========================================================================
   NETZWERK — SENSOR-DIREKTABRUF (zukünftig)
   ========================================================================== */

async function fetchSensor() {
  if (!SENSOR_ENDPOINT) return;
  try {
    const j = await fetch(SENSOR_ENDPOINT, { cache: 'no-store', signal: AbortSignal.timeout(2000) }).then(r => r.json());
    state.sensorSpindle = j.spindle_A ?? null;
    state.sensorX = j.x_axis_A ?? null;
    state.sensorY = j.y_axis_A ?? null;
    const push = (arr, v) => { arr.push(v); if (arr.length > 40) arr.shift(); };
    push(state.sensorHist.s, state.sensorSpindle);
    push(state.sensorHist.x, state.sensorX);
    push(state.sensorHist.y, state.sensorY);
    ['ch-spindle','ch-x','ch-y'].forEach(id => document.getElementById(id)?.classList.add('live'));
    updateSensorUI();
  } catch (_) {}
}

/* ==========================================================================
   VERBINDUNGS-MODAL — Initialisierung
   ========================================================================== */

let bsModal;

window.addEventListener('DOMContentLoaded', () => {
  bsModal = new bootstrap.Modal(document.getElementById('configModal'));

  // Gespeicherte Werte aus dem letzten Browserbesuch wiederherstellen
  const saved = {
    base:    localStorage.getItem('mtc_base'),
    proxy:   localStorage.getItem('mtc_proxy'),
    backend: localStorage.getItem('mtc_backend'),
    sensor:  localStorage.getItem('mtc_sensor'),
  };

  // Felder im Modal vorausfüllen
  const set = (id, val) => { const e = document.getElementById(id); if (e && val) e.value = val; };
  set('inputIP',      saved.base);
  set('inputProxy',   saved.proxy);
  set('inputBackend', saved.backend);
  set('inputSensor',  saved.sensor);

  // Globale Variablen setzen
  BASE_URL        = saved.base    || null;
  BACKEND_URL     = saved.backend || null;
  SENSOR_ENDPOINT = saved.sensor  || null;

  // PROXY_URL: gespeicherten Wert ODER Seiten-Origin wenn über HTTP geladen
  // Wenn die Seite über http://localhost:8888/... geöffnet wurde, ist der
  // Proxy definitiv erreichbar — window.location.origin als Standard verwenden.
  // Das löst den Fall wo PROXY_URL noch nie gespeichert wurde.
  if (saved.proxy) {
    PROXY_URL = saved.proxy;
  } else if (window.location.protocol === "http:" &&
             (window.location.hostname === "localhost" ||
              window.location.hostname === "127.0.0.1")) {
    // Seite läuft auf localhost → cors_proxy.py bedient sie → gleicher Origin ist der Proxy
    PROXY_URL = window.location.origin;
    localStorage.setItem("mtc_proxy", PROXY_URL);
    // Feld im Modal vorausfüllen
    const proxyEl = document.getElementById("inputProxy");
    if (proxyEl) proxyEl.value = PROXY_URL;
  } else {
    PROXY_URL = null;
  }

  // Verbindung automatisch testen wenn etwas gespeichert ist
  if (PROXY_URL || BACKEND_URL || BASE_URL) {
    testConnection(true);  // silent = kein Modal-Feedback
  } else {
    bsModal.show();  // Nichts gespeichert → Modal anzeigen
  }

  setInterval(tickClock, 1000);
  tickClock();

  // Poll-Intervall — startet sofort, guards in poll() verhindern Leerlauf-Aufrufe
  setInterval(poll, POLL_MS);
});

/* ==========================================================================
   VERBINDUNGS-MODAL — applyConfig
   ========================================================================== */

/**
 * Wird vom VERBINDEN-Button aufgerufen.
 * Liest alle Eingabefelder aus, setzt die globalen URL-Variablen,
 * speichert sie im localStorage und startet den Verbindungstest.
 */
async function applyConfig() {
  const ip      = (document.getElementById('inputIP')?.value      || '').trim();
  const port    = (document.getElementById('inputPort')?.value    || '8082').trim();
  const proxy   = (document.getElementById('inputProxy')?.value   || '').trim();
  const backend = (document.getElementById('inputBackend')?.value || '').trim();
  const sensor  = (document.getElementById('inputSensor')?.value  || '').trim();

  // Mindestens eine URL muss angegeben sein
  if (!ip && !proxy && !backend) {
    const fb = document.getElementById('connFeedback');
    if (fb) {
      fb.className = 'alert alert-warning py-2 mb-0';
      fb.textContent = '⚠ Bitte mindestens PROXY URL oder Agent-IP eingeben.';
      fb.classList.remove('d-none');
    }
    return;
  }

  // MTConnect-Agent-URL zusammenbauen
  if (ip) {
    let base = ip.startsWith('http') ? ip : 'http://' + ip;
    if (!/:\d+/.test(base.replace(/^https?:\/\//, ''))) base += ':' + port;
    BASE_URL = base.replace(/\/$/, '');
    localStorage.setItem('mtc_base', BASE_URL);
  } else {
    BASE_URL = null;
    localStorage.removeItem('mtc_base');
  }

  // CORS-Proxy (cors_proxy.py)
  PROXY_URL = proxy ? proxy.replace(/\/$/, '') : null;
  if (PROXY_URL) localStorage.setItem('mtc_proxy',   PROXY_URL);
  else           localStorage.removeItem('mtc_proxy');

  // Python-Backend
  BACKEND_URL = backend ? backend.replace(/\/$/, '') : null;
  if (BACKEND_URL) localStorage.setItem('mtc_backend', BACKEND_URL);
  else             localStorage.removeItem('mtc_backend');

  // Stromsensor
  SENSOR_ENDPOINT = sensor || null;
  if (SENSOR_ENDPOINT) localStorage.setItem('mtc_sensor', SENSOR_ENDPOINT);
  else                 localStorage.removeItem('mtc_sensor');

  await testConnection(false);
}

/* ==========================================================================
   VERBINDUNGSTEST
   ========================================================================== */

/**
 * Testet die Verbindung durch Abruf von /probe.
 * Verwendet dieselbe Proxy-Routing-Logik wie fetchMTC().
 *
 * @param {boolean} silent - true = kein sichtbares Feedback im Modal
 */
async function testConnection(silent) {
  const fb = document.getElementById('connFeedback');

  // Probe-URL nach gleicher Priorität wie fetchMTC — BASE_URL nie direkt
  let probeUrl;
  if (PROXY_URL) {
    probeUrl = `${PROXY_URL}/proxy/probe`;
  } else if (BACKEND_URL) {
    probeUrl = `${BACKEND_URL}/proxy/probe`;
  } else {
    // Weder Proxy noch Backend → direkte Probe (wird CORS-blockiert bei Haas)
    if (BASE_URL) probeUrl = `${BASE_URL}/probe`;
    else {
      if (!silent && fb) {
        fb.className = 'alert alert-warning py-2 mb-0';
        fb.textContent = '⚠ PROXY URL oder BACKEND URL eingeben (IP allein reicht nicht).';
        fb.classList.remove('d-none');
      }
      return;
    }
  }

  const displayLabel = BASE_URL || PROXY_URL || BACKEND_URL;

  if (!silent && fb) {
    fb.className = 'alert alert-secondary py-2 mb-0';
    fb.textContent = `Verbinde mit ${displayLabel} …`;
    fb.classList.remove('d-none');
  }

  try {
    const r = await fetch(probeUrl, { cache: 'no-store', signal: AbortSignal.timeout(4000) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);

    // Erfolg
    const disp = document.getElementById('ipDisplay');
    if (disp) disp.textContent = displayLabel;

    if (!silent && fb) {
      fb.className = 'alert alert-success py-2 mb-0';
      fb.textContent = `✔ Verbunden — ${displayLabel}`;
      setTimeout(() => bsModal.hide(), 800);
    }
    poll();  // Sofort ersten Datenabruf starten

  } catch (e) {
    // Hilfreicher Hinweis je nach konfigurierter Option
    let hint = '';
    if (PROXY_URL)        hint = ` — Läuft cors_proxy.py auf ${PROXY_URL}?`;
    else if (BACKEND_URL) hint = ` — Läuft das Backend auf ${BACKEND_URL}?`;
    else                  hint = ' — Agent-IP / Port prüfen';

    if (!silent && fb) {
      fb.className = 'alert alert-danger py-2 mb-0';
      fb.textContent = `✖ ${e.message}${hint}`;
      fb.classList.remove('d-none');
    } else {
      const disp = document.getElementById('ipDisplay');
      if (disp) disp.textContent = `${displayLabel} (verbinde erneut…)`;
    }
  }
}

/* ==========================================================================
   UHRZEIT
   ========================================================================== */

function tickClock() {
  const e = document.getElementById('wallClock');
  if (e) e.textContent = new Date().toTimeString().slice(0, 8);
}

/* ==========================================================================
   XML-PARSING — /current
   ========================================================================== */

function parseCurrent(doc) {
  if (!doc) return;

  const rpm = xmlAll(doc, 'SpindleSpeed', 'SpindleSpeed');
  if (rpm.length) state.spindleRPM = parseFloat(rpm[rpm.length - 1].value) || 0;

  const x = xmlAll(doc, 'PathPosition', 'X_Axis_Actual_Position');
  if (x.length) state.xPos = parseFloat(x[x.length - 1].value) || 0;
  const y = xmlAll(doc, 'PathPosition', 'Y_Axis_Actual_Position');
  if (y.length) state.yPos = parseFloat(y[y.length - 1].value) || 0;

  const spn = xmlGet(doc, 'Message', 'SpindleNumber');    if (spn) state.spNum   = spn;
  const mp  = xmlGet(doc, 'Message', 'SpindleMaxPower');  if (mp)  state.maxPwr  = parseFloat(mp);
  const stn = xmlGet(doc, 'Message', 'STATION');          if (stn) state.station = stn;

  const dhmt = xmlGet(doc, 'Message', 'DHMT_Codes');
  if (dhmt) { const p = dhmt.split(','); if (p[2]) state.toolNum = parseInt(p[2]) || state.toolNum; }

  const ss = xmlAll(doc, 'SpindleSpeed',  'SpindleSpeedOverride'); if (ss.length) state.ssovrd = ss[0].value + '%';
  const fd = xmlAll(doc, 'PathFeedrate',  'FeedrateOverride');      if (fd.length) state.fdovrd = fd[0].value + '%';
  const ro = xmlAll(doc, 'AxisFeedrate',  'RapidOverride');         if (ro.length) state.rovrd  = ro[0].value + '%';

  const gc = xmlGet(doc, 'Message', 'Gcodes');
  if (gc) state.gcodes = gc.split(',').map(s => s.trim()).filter(Boolean);
  const pr = xmlGet(doc, 'Program',  'Program');             if (pr) state.program = pr;
  const m3 = xmlGet(doc, 'Message',  'M30Counter1');         if (m3) state.m30c    = m3;
  const mr = xmlGet(doc, 'Message',  'MachineRunTime');      if (mr) state.machineRunTime = parseInt(mr) || 0;
  const st = xmlGet(doc, 'Message',  'SpindleTime');         if (st) state.spindleTime    = parseInt(st) || 0;
  const al = xmlGet(doc, 'Message',  'ActiveAlarms');        if (al) state.activeAlarms   = al;
  const ct = xmlAll(doc, 'AccumulatedTime', 'ThisCycle');
  if (ct.length) state.cycleTime = parseInt(ct[ct.length - 1].value) || 0;
}

/* ==========================================================================
   XML-PARSING — /sample
   ========================================================================== */

function parseSamples(doc) {
  if (!doc) return;

  const xEls = xmlAll(doc, 'PathPosition', 'X_Axis_Actual_Position');
  const yEls = xmlAll(doc, 'PathPosition', 'Y_Axis_Actual_Position');

  if (xEls.length && yEls.length) {
    const len = Math.min(xEls.length, yEls.length);
    state.posHistory = [];
    for (let i = 0; i < len; i++)
      state.posHistory.push({ x: parseFloat(xEls[i].value), y: parseFloat(yEls[i].value), z: 0 });
    if (len >= 2) {
      state.velX = Math.abs(state.posHistory[len-1].x - state.posHistory[len-2].x);
      state.velY = Math.abs(state.posHistory[len-1].y - state.posHistory[len-2].y);
    }
    state.xPos = state.posHistory[state.posHistory.length - 1]?.x ?? state.xPos;
    state.yPos = state.posHistory[state.posHistory.length - 1]?.y ?? state.yPos;
  }

  const ct = xmlAll(doc, 'AccumulatedTime', 'ThisCycle');  if (ct.length) state.cycleTime      = parseInt(ct[ct.length-1].value) || 0;
  const rp = xmlAll(doc, 'SpindleSpeed',    'SpindleSpeed'); if (rp.length) state.spindleRPM   = parseFloat(rp[rp.length-1].value) || 0;
  const mr = xmlAll(doc, 'Message', 'MachineRunTime');       if (mr.length) state.machineRunTime = parseInt(mr[mr.length-1].value) || 0;
  const sp = xmlAll(doc, 'Message', 'SpindleTime');          if (sp.length) state.spindleTime    = parseInt(sp[sp.length-1].value) || 0;
}

/* ==========================================================================
   HAUPT-POLL-SCHLEIFE
   ==========================================================================
   Läuft jede Sekunde. Wählt die Datenquelle basierend auf gesetzten URLs.
   BASE_URL wird NIE direkt für XML-Abrufe genutzt wenn Proxy/Backend gesetzt.
   ========================================================================== */

async function poll() {
  // Nichts konfiguriert → überspringen
  if (!PROXY_URL && !BACKEND_URL) return;

  try {
    // ── Priorität 1: Python-Backend → /api/latest (JSON) ─────────────────
    if (BACKEND_URL) {
      const ok = await fetchFromBackend();
      if (ok) { renderUI(); return; }
      // Backend nicht erreichbar → fallback auf Proxy-XML (Priorität 2)
    }

    // ── Priorität 2: XML über Proxy ───────────────────────────────────────
    // fetchMTC() gibt null zurück wenn PROXY_URL und BACKEND_URL beide null.
    // Der Guard oben stellt sicher dass das hier nie passiert.
    const [docCurrent, docSample] = await Promise.all([
      fetchMTC('current'),
      fetchMTC('sample'),
    ]);

    if (docCurrent || docSample) {
      parseCurrent(docCurrent);
      parseSamples(docSample);
      await fetchSensor();
      renderUI();
    }
  } catch (e) {
    console.error('Poll-Fehler:', e);
  }
}

/* ==========================================================================
   HILFSFUNKTIONEN
   ========================================================================== */

const el   = id => document.getElementById(id);
const fmtMM  = s => `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
const fmtHMS = s => { const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60; return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`; };

/* ==========================================================================
   UI-RENDERING — Hauptdashboard
   ========================================================================== */

function renderUI() {
  // ── Übersicht-Tab ──────────────────────────────────────────────────────
  el('spindleVal').textContent      = Math.round(state.spindleRPM);
  el('toolNum').textContent         = state.toolNum;
  el('station').textContent         = state.station;
  el('spNum').textContent           = state.spNum;
  el('maxPwr').textContent          = state.maxPwr + ' kW';
  el('ssovrd').textContent          = state.ssovrd;
  el('fdovrd').textContent          = state.fdovrd;
  el('cycleTimer').textContent      = fmtMM(state.cycleTime);
  el('cycleRaw').textContent        = state.cycleTime + ' s';
  el('spindleTimeDisp').textContent = fmtHMS(state.spindleTime);
  el('machRT').textContent          = fmtHMS(state.machineRunTime);
  el('xPos').textContent            = state.xPos.toFixed(3) + ' mm';
  el('yPos').textContent            = state.yPos.toFixed(3) + ' mm';
  el('zPos').textContent            = '—';
  el('progName').textContent        = state.program || '—';
  el('m30c').textContent            = state.m30c;

  el('gcodesWrap').innerHTML = state.gcodes.map(g =>
    `<span class="gcode-chip${['G03','G43','G64'].includes(g)?' hl':''}">${g}</span>`
  ).join('') || '—';

  const alarmsOk = !state.activeAlarms || state.activeAlarms === 'NO ACTIVE ALARMS';
  el('alertBar').className = `alert d-flex align-items-center gap-2 py-2 mb-3 ${alarmsOk ? 'alert-success border-success' : 'alert-danger border-danger'}`;
  el('alertBar').innerHTML = `<i class="bi bi-${alarmsOk ? 'check-circle-fill' : 'exclamation-triangle-fill'}"></i><span>${alarmsOk ? (typeof t==='function' ? t('alertOk') : 'MASCHINENZUSTAND NORMAL | KEINE AKTIVEN ALARME') : state.activeAlarms}</span>`;

  // ── Achsleistung-Tab ───────────────────────────────────────────────────
  el('velXVal').textContent  = state.velX.toFixed(2);
  el('velYVal').textContent  = state.velY.toFixed(2);
  el('velSVal').textContent  = Math.round(state.spindleRPM);
  el('fdovrd2').textContent  = state.fdovrd;
  el('rovrd2').textContent   = state.rovrd;
  el('machRT2').textContent  = fmtHMS(state.machineRunTime);
  el('barX').style.width = Math.min(100, state.velX / 169.3 * 100).toFixed(1) + '%';
  el('barY').style.width = Math.min(100, state.velY / 169.3 * 100).toFixed(1) + '%';
  el('barS').style.width = Math.min(100, state.spindleRPM / 6000 * 100).toFixed(1) + '%';

  // ── Trajektorie-Tab ────────────────────────────────────────────────────
  el('xyX').textContent = state.xPos.toFixed(2);
  el('xyY').textContent = state.yPos.toFixed(2);
  el('xzX').textContent = state.xPos.toFixed(2);
  el('xzZ').textContent = '—';
  el('yzY').textContent = state.yPos.toFixed(2);
  el('yzZ').textContent = '—';
  el('lastUpdate').textContent = (typeof t==='function' ? t('lastUpdateLabel') : 'Letzte Aktualisierung:') + ' ' + new Date().toLocaleTimeString();

  // ── Canvas ────────────────────────────────────────────────────────────
  drawTacho('spindleTacho', state.spindleRPM, 6000,  '#0dcaf0');
  drawTacho('tachoX',       state.velX,       169.3, '#0dcaf0');
  drawTacho('tachoY',       state.velY,       169.3, '#20c997');
  drawTacho('tachoS',       state.spindleRPM, 6000,  '#0dcaf0');
  drawTrajectory('viewXY', state.posHistory, 'x', 'y');
  drawTrajectory('viewXZ', state.posHistory, 'x', 'z');
  drawTrajectory('viewYZ', state.posHistory, 'y', 'z');
}

/* ==========================================================================
   UI-RENDERING — Stromsensor-Panel
   ========================================================================== */

function updateSensorUI() {
  const fmt = v => v !== null ? v.toFixed(2) : '—.—';
  el('sensorSpindle').textContent = fmt(state.sensorSpindle);
  el('sensorX').textContent       = fmt(state.sensorX);
  el('sensorY').textContent       = fmt(state.sensorY);
  if (state.sensorSpindle !== null) {
    el('derPower').textContent = Math.round(1.732 * 480 * state.sensorSpindle) + ' W';
    el('derRMS').textContent   = state.sensorSpindle.toFixed(2) + ' A';
    el('derPeak').textContent  = (state.sensorSpindle * 1.414).toFixed(2) + ' A';
    el('derLoad').textContent  = Math.min(100, Math.round(state.sensorSpindle / 30 * 100)) + ' %';
    ['derPower','derRMS','derPeak','derLoad'].forEach(id => el(id).className = 'val-lg text-accent2');
  }
  drawMiniChart('chartSpindle', state.sensorHist.s, '#0dcaf0');
  drawMiniChart('chartX',       state.sensorHist.x, '#20c997');
  drawMiniChart('chartY',       state.sensorHist.y, '#fd7e14');
}

/* ==========================================================================
   CANVAS — Tachometer
   ========================================================================== */

function drawTacho(id, value, maxVal, color) {
  const c = el(id);
  if (!c) return;
  const ctx = c.getContext('2d');
  const W = c.width, H = c.height;
  const cx = W/2, cy = H-14;
  const r  = Math.min(W, H*2) * 0.43;
  ctx.clearRect(0, 0, W, H);
  const frac = Math.min(1, Math.max(0, value / maxVal));
  const sA = Math.PI, valA = sA + frac * Math.PI;

  // Hintergrundtrack
  ctx.beginPath(); ctx.arc(cx, cy, r, sA, 0, false);
  ctx.strokeStyle = '#1a2535'; ctx.lineWidth = 16; ctx.stroke();

  // Skalenstriche
  for (let i = 0; i <= 10; i++) {
    const a = Math.PI + (i/10)*Math.PI;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a)*(r-22), cy + Math.sin(a)*(r-22));
    ctx.lineTo(cx + Math.cos(a)*(r-(i%5===0?6:12)), cy + Math.sin(a)*(r-(i%5===0?6:12)));
    ctx.strokeStyle = i%5===0 ? '#3a4a5c' : '#1e2d40'; ctx.lineWidth = i%5===0?2:1; ctx.stroke();
  }

  // Halo
  ctx.beginPath(); ctx.arc(cx, cy, r, sA, valA, false);
  ctx.strokeStyle = color+'33'; ctx.lineWidth = 28; ctx.stroke();

  // Wertbogen
  const grad = ctx.createLinearGradient(cx-r, cy, cx+r, cy);
  grad.addColorStop(0, color); grad.addColorStop(1, frac>0.75 ? '#dc3545' : color);
  ctx.beginPath(); ctx.arc(cx, cy, r, sA, valA, false);
  ctx.strokeStyle = grad; ctx.lineWidth = 14; ctx.lineCap = 'round'; ctx.stroke();

  // Nadel
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(valA);
  ctx.beginPath(); ctx.moveTo(-6, 0); ctx.lineTo(r-8, 0);
  ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.shadowColor = color; ctx.shadowBlur = 8; ctx.stroke();
  ctx.restore();

  // Mittelpunkt
  ctx.beginPath(); ctx.arc(cx, cy, 6, 0, Math.PI*2);
  ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 12; ctx.fill(); ctx.shadowBlur = 0;

  // Beschriftungen
  ctx.fillStyle = '#3a4a5c'; ctx.font = '10px "Share Tech Mono"';
  ctx.textAlign = 'right'; ctx.fillText('0', cx-r-4, cy+14);
  ctx.textAlign = 'left';  ctx.fillText(maxVal < 200 ? maxVal.toFixed(0) : (maxVal/1000).toFixed(1)+'k', cx+r+4, cy+14);
}

/* ==========================================================================
   CANVAS — Bahnkurven-Plot
   ========================================================================== */

function drawTrajectory(id, history, axisH, axisV) {
  const c = el(id);
  if (!c) return;
  c.width  = c.offsetWidth  || 300;
  c.height = c.offsetHeight || 240;
  const ctx = c.getContext('2d');
  const W = c.width, H = c.height, PAD = 24;
  ctx.clearRect(0, 0, W, H);

  // Gitter
  ctx.strokeStyle = '#131c28'; ctx.lineWidth = 1;
  for (let x = PAD; x < W-PAD; x += 40) { ctx.beginPath(); ctx.moveTo(x,PAD); ctx.lineTo(x,H-PAD); ctx.stroke(); }
  for (let y = PAD; y < H-PAD; y += 40) { ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-PAD,y); ctx.stroke(); }

  // Achsenbeschriftungen
  ctx.fillStyle = '#3a4a5c'; ctx.font = '10px sans-serif';
  ctx.textAlign = 'center'; ctx.fillText(axisH.toUpperCase(), W/2, H-6);
  ctx.save(); ctx.translate(10, H/2); ctx.rotate(-Math.PI/2); ctx.fillText(axisV.toUpperCase(), 0, 0); ctx.restore();

  if (!history.length) {
    ctx.fillStyle = '#1e2d40'; ctx.font = '11px "Share Tech Mono"'; ctx.textAlign = 'center';
    ctx.fillText(typeof t==='function' ? t('canvasAwait') : 'AWAITING DATA', W/2, H/2);
    return;
  }

  const vh = history.map(p => p[axisH] || 0), vv = history.map(p => p[axisV] || 0);
  const mnH=Math.min(...vh), mxH=Math.max(...vh), rH=mxH-mnH||1;
  const mnV=Math.min(...vv), mxV=Math.max(...vv), rV=mxV-mnV||1;
  const toX = h => PAD + ((h-mnH)/rH)*(W-2*PAD);
  const toY = v => H-PAD - ((v-mnV)/rV)*(H-2*PAD);
  const pts = history.map(p => ({ px: toX(p[axisH]||0), py: toY(p[axisV]||0) }));

  // Fading trail
  for (let i = 1; i < pts.length; i++) {
    const alpha = 0.15 + 0.85*(i/pts.length);
    ctx.beginPath(); ctx.moveTo(pts[i-1].px, pts[i-1].py); ctx.lineTo(pts[i].px, pts[i].py);
    ctx.strokeStyle = `rgba(13,202,240,${alpha})`; ctx.lineWidth = 1.5; ctx.stroke();
  }

  // Aktueller Punkt
  const last = pts[pts.length-1];
  ctx.beginPath(); ctx.arc(last.px, last.py, 5, 0, Math.PI*2);
  ctx.fillStyle = '#20c997'; ctx.shadowColor = '#20c997'; ctx.shadowBlur = 12; ctx.fill(); ctx.shadowBlur = 0;

  // Eckenkoordinaten
  ctx.fillStyle = '#1e2d40'; ctx.font = '9px "Share Tech Mono"';
  ctx.textAlign = 'left';  ctx.fillText(mnH.toFixed(1), PAD, H-PAD+14);
  ctx.textAlign = 'right'; ctx.fillText(mxH.toFixed(1), W-PAD, H-PAD+14);
  ctx.fillText(mxV.toFixed(1), PAD-2, PAD+10);
  ctx.fillText(mnV.toFixed(1), PAD-2, H-PAD);
}

/* ==========================================================================
   CANVAS — Sparkline
   ========================================================================== */

function drawMiniChart(id, data, color) {
  const c = el(id);
  if (!c || !data.length) return;
  c.width = c.offsetWidth || 200;
  const ctx = c.getContext('2d');
  const W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  const mn=Math.min(...data), mx=Math.max(...data), rng=mx-mn||1;
  const pts = data.map((v,i) => ({ x:(i/(data.length-1))*W, y:H-2-((v-mn)/rng)*(H-4) }));
  ctx.beginPath();
  pts.forEach((p,i) => i===0 ? ctx.moveTo(p.x,p.y) : ctx.lineTo(p.x,p.y));
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath();
  ctx.fillStyle = color+'22'; ctx.fill();
}
