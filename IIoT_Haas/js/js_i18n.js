/**
 * i18n.js — Internationalisierung (Mehrsprachigkeit)
 * ====================================================
 * Unterstützte Sprachen: Englisch (en), Deutsch (de), Spanisch (es)
 *
 * Funktionsweise:
 *   1. Alle übersetzbaren Texte im HTML tragen das Attribut data-i18n="schluessel"
 *   2. setLanguage(lang) wird beim Klick auf EN / DE / ES aufgerufen
 *   3. Die Funktion sucht alle Elemente mit data-i18n und ersetzt deren Inhalt
 *      mit dem passenden Text aus dem i18n-Dictionary
 *   4. Die gewählte Sprache wird in localStorage gespeichert und beim nächsten
 *      Seitenaufruf automatisch wiederhergestellt
 *
 * Neue Texte hinzufügen:
 *   1. data-i18n="neuerSchluessel" im HTML-Element einfügen
 *   2. Den Schlüssel mit Übersetzungen in alle drei Sprachobjekte eintragen
 */

'use strict';


/* ============================================================
   ÜBERSETZUNGS-DICTIONARY
   ============================================================
   Struktur: i18n[sprache][schluessel] = "übersetzter Text"
   Schlüssel stimmen 1:1 mit den data-i18n-Attributen im HTML überein.
   HTML-Tags (z.B. <code>) sind in den Strings erlaubt → werden per
   innerHTML eingefügt, nicht per textContent.
   ============================================================ */

const i18n = {

  /* ── ENGLISCH ──────────────────────────────────────────────────────── */
  en: {
    // Seite und Modal
    pageTitle:        'CNC Milling Dashboard — TM-1P',
    modalTitle:       'MTCONNECT ENDPOINT',
    modalDesc:        'Enter device IP — data is fetched from <code>/sample</code>, <code>/current</code> and <code>/probe</code>',
    labelIP:          'DEVICE IP ADDRESS',
    labelPort:        'PORT',
    labelPortHint:    '(default 5000)',
    labelSensor:      'CURRENT SENSOR URL',
    labelSensorHint:  '(optional — future)',
    labelBackend:     'PYTHON BACKEND URL',
    labelBackendHint: '(optional — enables DB history)',
    btnConnect:       'CONNECT',

    // Navbar
    navTitle:         'MILLING PROCESS MONITOR',
    statusActive:     'ACTIVE',
    statusNoAlarm:    'NO ALARMS',

    // Tabs
    tabOverview:      'OVERVIEW',
    tabAxes:          'AXIS POWER',
    tabTrajectory:    'TRAJECTORY',
    tabSensor:        'CURRENT SENSOR',

    // Übersicht-Tab
    alertOk:          'MACHINE CONDITION NORMAL &nbsp;|&nbsp; NO ACTIVE ALARMS',
    cardSpindle:      'SPINDLE',
    unitRPM:          'RPM',
    max:              'max',
    cardActiveTool:   'ACTIVE TOOL',
    lblToolNum:       'Tool #',
    lblStation:       'Station',
    lblSpindleNum:    'Spindle #',
    lblMaxPower:      'Max Power',
    lblSpindleOvrd:   'Spindle Ovrd',
    lblFeedOvrd:      'Feed Ovrd',
    cardCycleTime:    'CYCLE TIME',
    lblThisCycle:     'THIS CYCLE',
    lblSpindleTime:   'SPINDLE TIME',
    lblMachineRuntime:'MACHINE RUNTIME',
    cardProgram:      'PROGRAM',
    lblActiveProgram: 'ACTIVE PROGRAM',
    lblM30:           'M30 Counter:',
    lblActiveGcodes:  'ACTIVE G-CODES',
    cardAxisPos:      'AXIS POSITIONS',
    thAxis:           'AXIS',
    thPosition:       'ACTUAL (mm)',
    thMaxSpeed:       'MAX SPEED',
    thLimit:          'LIMIT (–)',
    thStatus:         'STATUS',
    badgeEnabled:     'ENABLED',

    // Achsleistung-Tab
    axisX:            'X AXIS',
    axisY:            'Y AXIS',
    lblVelocity:      'VELOCITY',
    cardMachineParams:'MACHINE PARAMETERS',
    lblMaxFeed:       'Max Feed Rate',
    lblMaxAccel:      'Max Acceleration',
    lblContourAccel:  'Contour Max Accel',
    lblTimeConst:     'Time Constant',
    lblCornerRound:   'Corner Rounding',
    lblRapidOvrd:     'Rapid Override',

    // Trajektorie-Tab
    trajectoryHint:   'Tool path trace from position history &nbsp;·&nbsp; Green dot = current position',
    viewTop:          '(TOP)',
    viewFront:        '(FRONT)',
    viewSide:         '(SIDE)',

    // Stromsensor-Tab
    sensorNotConnected: 'SENSOR DATA STREAM NOT CONNECTED &nbsp;|&nbsp; INTERFACE READY',
    sensorSpindleCh:  'SPINDLE CURRENT',
    sensorXCh:        'X AXIS CURRENT',
    sensorYCh:        'Y AXIS CURRENT',
    unitAmpere:       'AMPERE (A)',
    cardComputedMetrics: 'COMPUTED METRICS',
    metricsHint:      '(active once sensor is live)',
    lblPowerEst:      'Power Estimate',
    lblRMS:           'RMS Current',
    lblPeak:          'Peak Current',
    lblLoad:          'Load %',
    cardIntegration:  'INTEGRATION GUIDE',
    codeComment1:     'Set sensor URL in ⚙ modal or in app.js:',
    codeComment2:     'Expected JSON format:',
    codeComment3:     'Dashboard polls automatically every 1 s.',

    // Canvas-Texte (können kein HTML enthalten)
    canvasAwait:      'AWAITING DATA',

    // Fußzeile
    footerNotConfigured: 'NOT CONFIGURED',
    footerRefresh:    'UPDATING EVERY 1 s',
    footerLastUpdate: 'Last update: —',
    lastUpdateLabel:  'Last update:',
  },


  /* ── DEUTSCH ───────────────────────────────────────────────────────── */
  de: {
    // Seite und Modal
    pageTitle:        'CNC Fräsmaschinen-Dashboard — TM-1P',
    modalTitle:       'MTCONNECT ENDPUNKT',
    modalDesc:        'Gerät-IP eingeben — Daten werden von <code>/sample</code>, <code>/current</code> und <code>/probe</code> gelesen',
    labelIP:          'GERÄT IP-ADRESSE',
    labelPort:        'PORT',
    labelPortHint:    '(Standard 5000)',
    labelSensor:      'STROMSENSOR-URL',
    labelSensorHint:  '(optional — zukünftig)',
    labelBackend:     'PYTHON BACKEND URL',
    labelBackendHint: '(optional — aktiviert DB-Verlauf)',
    btnConnect:       'VERBINDEN',

    // Navbar
    navTitle:         'FRÄSMASCHINEN-PROZESS-MONITOR',
    statusActive:     'AKTIV',
    statusNoAlarm:    'KEIN ALARM',

    // Tabs
    tabOverview:      'ÜBERSICHT',
    tabAxes:          'ACHSLEISTUNG',
    tabTrajectory:    'TRAJEKTORIE',
    tabSensor:        'STROMSENSOR',

    // Übersicht-Tab
    alertOk:          'MASCHINENZUSTAND NORMAL &nbsp;|&nbsp; KEINE AKTIVEN ALARME',
    cardSpindle:      'SPINDEL',
    unitRPM:          'U/MIN',
    max:              'max',
    cardActiveTool:   'AKTIVES WERKZEUG',
    lblToolNum:       'Werkzeug-Nr.',
    lblStation:       'Station',
    lblSpindleNum:    'Spindel-Nr.',
    lblMaxPower:      'Max. Leistung',
    lblSpindleOvrd:   'Spindel-Override',
    lblFeedOvrd:      'Vorschub-Override',
    cardCycleTime:    'ZYKLUSZEIT',
    lblThisCycle:     'DIESER ZYKLUS',
    lblSpindleTime:   'SPINDELLAUFZEIT',
    lblMachineRuntime:'MASCHINENLAUFZEIT',
    cardProgram:      'PROGRAMM',
    lblActiveProgram: 'AKTIVES PROGRAMM',
    lblM30:           'M30-Zähler:',
    lblActiveGcodes:  'AKTIVE G-CODES',
    cardAxisPos:      'ACHSEN POSITIONEN',
    thAxis:           'ACHSE',
    thPosition:       'POSITION (mm)',
    thMaxSpeed:       'MAX. GESCHW.',
    thLimit:          'LIMIT (–)',
    thStatus:         'STATUS',
    badgeEnabled:     'AKTIV',

    // Achsleistung-Tab
    axisX:            'X-ACHSE',
    axisY:            'Y-ACHSE',
    lblVelocity:      'GESCHWINDIGKEIT',
    cardMachineParams:'MASCHINENPARAMETER',
    lblMaxFeed:       'Max. Vorschubrate',
    lblMaxAccel:      'Max. Beschleunigung',
    lblContourAccel:  'Max. Kontur-Beschl.',
    lblTimeConst:     'Zeitkonstante',
    lblCornerRound:   'Eckenrundung',
    lblRapidOvrd:     'Eilgang-Override',

    // Trajektorie-Tab
    trajectoryHint:   'Werkzeugbahn aus Positionsverlauf &nbsp;·&nbsp; Grüner Punkt = aktuelle Position',
    viewTop:          '(DRAUFSICHT)',
    viewFront:        '(FRONTANSICHT)',
    viewSide:         '(SEITENANSICHT)',

    // Stromsensor-Tab
    sensorNotConnected: 'SENSORDATEN NOCH NICHT VERBUNDEN &nbsp;|&nbsp; INTERFACE VORBEREITET',
    sensorSpindleCh:  'SPINDELSTROM',
    sensorXCh:        'X-ACHSEN-STROM',
    sensorYCh:        'Y-ACHSEN-STROM',
    unitAmpere:       'AMPERE (A)',
    cardComputedMetrics: 'BERECHNETE KENNWERTE',
    metricsHint:      '(nach Sensorverbindung aktiv)',
    lblPowerEst:      'Leistungsschätzung',
    lblRMS:           'Effektivwert (RMS)',
    lblPeak:          'Spitzenstrom',
    lblLoad:          'Auslastung',
    cardIntegration:  'INTEGRATIONSANLEITUNG',
    codeComment1:     'Sensor-URL im ⚙ Modal eingeben oder in app.js setzen:',
    codeComment2:     'Erwartetes JSON-Format:',
    codeComment3:     'Dashboard fragt automatisch jede Sekunde ab.',

    // Canvas-Texte
    canvasAwait:      'WARTE AUF DATEN',

    // Fußzeile
    footerNotConfigured: 'NICHT KONFIGURIERT',
    footerRefresh:    'AKTUALISIERUNG ALLE 1 s',
    footerLastUpdate: 'Letzte Aktualisierung: —',
    lastUpdateLabel:  'Letzte Aktualisierung:',
  },


  /* ── SPANISCH ──────────────────────────────────────────────────────── */
  es: {
    // Página y Modal
    pageTitle:        'Panel CNC Fresadora — TM-1P',
    modalTitle:       'PUNTO DE ACCESO MTCONNECT',
    modalDesc:        'Ingrese la IP del dispositivo — los datos se leen de <code>/sample</code>, <code>/current</code> y <code>/probe</code>',
    labelIP:          'DIRECCIÓN IP DEL DISPOSITIVO',
    labelPort:        'PUERTO',
    labelPortHint:    '(predeterminado 5000)',
    labelSensor:      'URL SENSOR DE CORRIENTE',
    labelSensorHint:  '(opcional — futuro)',
    labelBackend:     'URL BACKEND PYTHON',
    labelBackendHint: '(opcional — activa historial BD)',
    btnConnect:       'CONECTAR',

    // Barra de navegación
    navTitle:         'MONITOR DE PROCESO DE FRESADO',
    statusActive:     'ACTIVO',
    statusNoAlarm:    'SIN ALARMAS',

    // Pestañas
    tabOverview:      'RESUMEN',
    tabAxes:          'POTENCIA DE EJES',
    tabTrajectory:    'TRAYECTORIA',
    tabSensor:        'SENSOR DE CORRIENTE',

    // Pestaña Resumen
    alertOk:          'CONDICIÓN NORMAL &nbsp;|&nbsp; SIN ALARMAS ACTIVAS',
    cardSpindle:      'HUSILLO',
    unitRPM:          'RPM',
    max:              'máx',
    cardActiveTool:   'HERRAMIENTA ACTIVA',
    lblToolNum:       'N.º Herramienta',
    lblStation:       'Estación',
    lblSpindleNum:    'N.º Husillo',
    lblMaxPower:      'Potencia Máx.',
    lblSpindleOvrd:   'Override Husillo',
    lblFeedOvrd:      'Override Avance',
    cardCycleTime:    'TIEMPO DE CICLO',
    lblThisCycle:     'ESTE CICLO',
    lblSpindleTime:   'TIEMPO HUSILLO',
    lblMachineRuntime:'TIEMPO MÁQUINA',
    cardProgram:      'PROGRAMA',
    lblActiveProgram: 'PROGRAMA ACTIVO',
    lblM30:           'Contador M30:',
    lblActiveGcodes:  'G-CÓDIGOS ACTIVOS',
    cardAxisPos:      'POSICIONES DE EJES',
    thAxis:           'EJE',
    thPosition:       'POSICIÓN (mm)',
    thMaxSpeed:       'VEL. MÁXIMA',
    thLimit:          'LÍMITE (–)',
    thStatus:         'ESTADO',
    badgeEnabled:     'ACTIVO',

    // Pestaña Potencia de Ejes
    axisX:            'EJE X',
    axisY:            'EJE Y',
    lblVelocity:      'VELOCIDAD',
    cardMachineParams:'PARÁMETROS DE MÁQUINA',
    lblMaxFeed:       'Avance Máximo',
    lblMaxAccel:      'Aceleración Máxima',
    lblContourAccel:  'Acel. Máx. Contorno',
    lblTimeConst:     'Constante de Tiempo',
    lblCornerRound:   'Redondeo de Esquina',
    lblRapidOvrd:     'Override Rápido',

    // Pestaña Trayectoria
    trajectoryHint:   'Trayectoria de herramienta &nbsp;·&nbsp; Punto verde = posición actual',
    viewTop:          '(VISTA SUPERIOR)',
    viewFront:        '(VISTA FRONTAL)',
    viewSide:         '(VISTA LATERAL)',

    // Pestaña Sensor de Corriente
    sensorNotConnected: 'FLUJO DE DATOS NO CONECTADO &nbsp;|&nbsp; INTERFAZ PREPARADA',
    sensorSpindleCh:  'CORRIENTE HUSILLO',
    sensorXCh:        'CORRIENTE EJE X',
    sensorYCh:        'CORRIENTE EJE Y',
    unitAmpere:       'AMPERIO (A)',
    cardComputedMetrics: 'MÉTRICAS CALCULADAS',
    metricsHint:      '(activo cuando el sensor esté conectado)',
    lblPowerEst:      'Estimación de Potencia',
    lblRMS:           'Corriente RMS',
    lblPeak:          'Corriente Pico',
    lblLoad:          'Carga %',
    cardIntegration:  'GUÍA DE INTEGRACIÓN',
    codeComment1:     'Configure la URL del sensor en el modal ⚙ o en app.js:',
    codeComment2:     'Formato JSON esperado:',
    codeComment3:     'El panel consulta automáticamente cada 1 s.',

    // Textos en Canvas
    canvasAwait:      'ESPERANDO DATOS',

    // Pie de página
    footerNotConfigured: 'NO CONFIGURADO',
    footerRefresh:    'ACTUALIZACIÓN CADA 1 s',
    footerLastUpdate: 'Última actualización: —',
    lastUpdateLabel:  'Última actualización:',
  },
};


/* ============================================================
   AKTUELLE SPRACHE
   ============================================================ */

// Aktuelle Sprache: aus localStorage laden oder Standardwert "de" verwenden
let currentLang = localStorage.getItem('dashboard_lang') || 'de';


/* ============================================================
   KERNFUNKTION: setLanguage(lang)
   ============================================================
   Wird aufgerufen von:
     - Den Sprachschalter-Buttons im HTML (onclick="setLanguage('en')")
     - Beim Seitenstart in initI18n() (automatische Wiederherstellung)
   ============================================================ */

/**
 * Setzt die Sprache des gesamten Dashboards.
 * @param {string} lang - Sprachcode: 'en', 'de' oder 'es'
 */
function setLanguage(lang) {

  // Prüfen ob die Sprache im Dictionary vorhanden ist
  if (!i18n[lang]) {
    console.warn('Unbekannte Sprache:', lang);
    return;   // Unbekannte Sprache → abbrechen
  }

  currentLang = lang;                              // Aktuelle Sprache merken
  localStorage.setItem('dashboard_lang', lang);   // In localStorage speichern (bleibt nach Neustart)

  // ── HTML lang-Attribut aktualisieren ─────────────────────────────────
  // Wichtig für Screenreader und Suchmaschinen
  document.documentElement.lang = lang;

  // ── Alle übersetzbaren Elemente im DOM finden und aktualisieren ───────
  // querySelectorAll gibt ALLE Elemente mit dem Attribut data-i18n zurück
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');   // Übersetzungsschlüssel aus dem Attribut lesen

    // Übersetzung für diesen Schlüssel und diese Sprache nachschlagen
    const translation = i18n[lang][key];

    if (translation === undefined) {
      // Schlüssel existiert in dieser Sprache nicht → Warnung ausgeben, Element unverändert lassen
      console.warn(`Übersetzung fehlt: [${lang}]["${key}"]`);
      return;
    }

    // Sonderbehandlung für den Seitentitel (<title>-Tag)
    // <title> hat kein innerHTML → textContent verwenden
    if (el.tagName === 'TITLE') {
      el.textContent = translation;
      document.title = translation;   // Browser-Tab-Beschriftung aktualisieren
      return;
    }

    // Prüfen ob die Übersetzung HTML-Tags enthält (z.B. <code>, <br>)
    // Wenn ja: innerHTML verwenden (rendert Tags)
    // Wenn nein: textContent verwenden (sicherer, verhindert XSS)
    if (translation.includes('<')) {
      el.innerHTML = translation;   // HTML-Inhalt ersetzen (Tags werden gerendert)
    } else {
      el.textContent = translation; // Nur Text (keine Tags möglich)
    }
  });

  // ── Sprachschalter-Buttons optisch aktualisieren ──────────────────────
  // Alle drei Buttons zurücksetzen, dann den aktiven hervorheben
  ['en', 'de', 'es'].forEach(l => {
    const btn = document.getElementById('lang-' + l);
    if (btn) {
      // 'active'-Klasse nur beim gewählten Button setzen
      btn.classList.toggle('active', l === lang);
    }
  });

  // ── Dynamisch gerenderte Texte in app.js aktualisieren ────────────────
  // renderUI() verwendet einige Texte direkt (z.B. alertBar.innerHTML).
  // Diese werden beim nächsten Poll-Zyklus automatisch in der neuen Sprache gerendert.
  // Für sofortige Aktualisierung: renderUI() aufrufen wenn sie verfügbar ist.
  if (typeof renderUI === 'function') {
    renderUI();   // typeof-Prüfung: verhindert Fehler wenn app.js noch nicht geladen ist
  }
}


/* ============================================================
   HILFSFUNKTION: t(key)
   ============================================================
   Kurzform für den Zugriff auf Übersetzungen aus JavaScript-Code.
   Kann von app.js aufgerufen werden für dynamisch generierte Texte.
   Beispiel: el('alertBar').textContent = t('alertOk');
   ============================================================ */

/**
 * Gibt die Übersetzung eines Schlüssels in der aktuellen Sprache zurück.
 * @param {string} key - Übersetzungsschlüssel
 * @returns {string} Übersetzter Text, oder der Schlüssel selbst wenn nicht gefunden
 */
function t(key) {
  return i18n[currentLang]?.[key] ?? key;   // Fallback: Schlüssel selbst zurückgeben
}


/* ============================================================
   INITIALISIERUNG: automatische Sprachwiederherstellung
   ============================================================ */

/**
 * Wird beim Laden der Seite aufgerufen.
 * Stellt die zuletzt gewählte Sprache aus localStorage wieder her.
 */
function initI18n() {
  // Gespeicherte Sprache laden (Standard: 'de' wenn noch nie gewählt)
  const savedLang = localStorage.getItem('dashboard_lang') || 'de';

  // Sprache anwenden (aktualisiert DOM + Buttons)
  setLanguage(savedLang);
}

// DOMContentLoaded: Initialisierung sobald das HTML vollständig geladen ist
// Muss VOR app.js ausgeführt werden → i18n.js wird im HTML vor app.js eingebunden
document.addEventListener('DOMContentLoaded', initI18n);
