#!/usr/bin/env python3
"""
cors_proxy.py — CNC-Dashboard: HTTP-Server + CORS-Proxy in einem Skript
========================================================================

WARUM DIESES SKRIPT NOTWENDIG IST
-----------------------------------
Das CORS-Problem hat zwei Schichten:

  Schicht 1 — "null"-Origin:
    Wenn index.html per Doppelklick (file:///) geöffnet wird, meldet
    der Browser als Herkunft "null". Browser behandeln "null"-Anfragen
    besonders restriktiv. localStorage ist unzuverlässig, und manche
    Browser blockieren alle ausgehenden Anfragen komplett.

    Lösung: Die HTML-Datei über einen echten HTTP-Server öffnen, damit
    der Browser http://localhost:8888 als Herkunft sieht, nicht "null".

  Schicht 2 — MTConnect-Agent sendet kein CORS-Header:
    Der Haas NGC-Controller antwortet auf fetch()-Anfragen ohne das
    "Access-Control-Allow-Origin: *" Header. Der Browser blockiert
    die Antwort bevor JavaScript sie sehen kann.

    Lösung: Alle MTConnect-Anfragen laufen über /proxy/... auf diesem
    Server. Server-zu-Server-Anfragen unterliegen NICHT der CORS-Regel.
    Der Proxy ergänzt den fehlenden Header bevor er die Antwort zurückgibt.

DIESES SKRIPT LÖST BEIDE PROBLEME IN EINEM:
    1. Stellt index.html, app.js, styles.css, i18n.js als
       http://localhost:8888/ bereit  →  kein "null"-Origin mehr
    2. Leitet /proxy/current, /proxy/sample, /proxy/probe
       server-seitig an den MTConnect-Agent weiter  →  kein CORS-Fehler

VERWENDUNG
----------
  # Einfachste Form (Agent-IP muss im Dashboard-Modal eingegeben werden):
  python cors_proxy.py

  # Mit eingebautem Agent (Agent-IP ist dann vorausgefüllt):
  python cors_proxy.py --mtc-host 148.205.38.221 --mtc-port 8082

  # Anderer Port:
  python cors_proxy.py --port 9000 --mtc-host 148.205.38.221 --mtc-port 8082

  Dann im Browser öffnen: http://localhost:8888
  Im Dashboard-Modal PROXY URL eingeben: http://localhost:8888

KEIN PIP-INSTALL NOTWENDIG — nur Python-Standardbibliotheken.
"""

import argparse
import http.server
import logging
import mimetypes
import os
import socketserver
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mill-proxy")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
CFG = {
    "port":      8888,    # Port dieses Servers
    "mtc_host":  None,    # MTConnect-Agent IP  (None = offen für alle)
    "mtc_port":  8082,    # MTConnect-Agent Port
    "timeout":   5,       # Timeout für Anfragen an den Agent (Sekunden)
    "serve_dir": None,    # Verzeichnis mit HTML-Dateien (wird beim Start gesetzt)
}

# ---------------------------------------------------------------------------
# MIME-Typen registrieren
# ---------------------------------------------------------------------------
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")

# ---------------------------------------------------------------------------
# Request-Handler
# ---------------------------------------------------------------------------

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """
    Ein Handler für zwei Aufgaben:

      Statische Dateien:
        GET /           → index.html
        GET /app.js     → app.js
        GET /styles.css → styles.css
        GET /i18n.js    → i18n.js
        (alle Dateien aus CFG["serve_dir"])

      MTConnect-Proxy:
        GET /proxy/current → http://<mtc_host>:<mtc_port>/current  + CORS-Header
        GET /proxy/sample  → http://<mtc_host>:<mtc_port>/sample   + CORS-Header
        GET /proxy/probe   → http://<mtc_host>:<mtc_port>/probe    + CORS-Header
        GET /proxy/<path>  → http://<mtc_host>:<mtc_port>/<path>   + CORS-Header
    """

    # Standard-Zugriffslog deaktivieren (eigenes Logging verwendet)
    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        """
        Preflight-Antwort für CORS.
        Browser senden OPTIONS bevor dem eigentlichen GET wenn die Herkunft
        von der Ziel-URL abweicht. Ohne diese Antwort blockiert der Browser
        den nachfolgenden GET.
        """
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        """Verarbeitet alle GET-Anfragen und leitet sie zur richtigen Handler-Methode."""
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path.startswith("/proxy/") or parsed.path == "/proxy":
            self._handle_proxy(parsed)
        else:
            self._handle_static(parsed)

    # ── MTConnect-Proxy ──────────────────────────────────────────────────────

    def _handle_proxy(self, parsed):
        """
        Leitet die Anfrage server-seitig an den MTConnect-Agent weiter.
        Ergänzt den fehlenden Access-Control-Allow-Origin-Header in der Antwort.
        """
        # Agent muss konfiguriert sein
        if not CFG["mtc_host"]:
            self._xml_error(503,
                "Proxy-Modus nicht aktiv. "
                "Starte mit: python cors_proxy.py --mtc-host <IP> --mtc-port <PORT>"
            )
            return

        # Pfad extrahieren: /proxy/current → current
        if parsed.path.startswith("/proxy/"):
            mtc_path = parsed.path[len("/proxy/"):]
        else:
            # /proxy ohne Pfad → Fehler
            self._xml_error(400, "Pfad fehlt. Verwende /proxy/current, /proxy/sample usw.")
            return

        # Ziel-URL zusammenbauen; Querystring (z.B. ?from=&count=) weiterleiten
        target = f"http://{CFG['mtc_host']}:{CFG['mtc_port']}/{mtc_path}"
        if parsed.query:
            target += "?" + parsed.query

        log.info("PROXY  →  %s", target)

        try:
            req  = urllib.request.Request(target, headers={"Accept": "application/xml, */*"})
            with urllib.request.urlopen(req, timeout=CFG["timeout"]) as resp:
                body         = resp.read()
                content_type = resp.headers.get("Content-Type", "application/xml")
                status       = resp.status

            log.info("PROXY  ←  %s  [%d]  %d bytes", target, status, len(body))

            # Antwort mit CORS-Header zurückschicken
            self.send_response(status)
            self.send_header("Content-Type",   content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",  "no-store")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        except urllib.error.URLError as e:
            log.warning("Agent nicht erreichbar: %s — %s", target, e)
            self._xml_error(502, f"Agent nicht erreichbar: {e.reason}")
        except TimeoutError:
            log.warning("Timeout: %s", target)
            self._xml_error(504, "Agent antwortet nicht (Timeout)")
        except Exception as e:
            log.error("Proxy-Fehler: %s", e, exc_info=True)
            self._xml_error(500, str(e))

    # ── Statische Dateien ────────────────────────────────────────────────────

    def _handle_static(self, parsed):
        """
        Liefert statische Dateien (HTML, JS, CSS) aus dem Dashboard-Verzeichnis.
        Das ist der entscheidende Teil: durch Auslieferung über HTTP statt file://
        erhält der Browser eine echte http://localhost-Herkunft statt "null".
        """
        # / → index.html
        path = parsed.path.lstrip("/") or "index.html"

        # Sicherheitscheck: Pfad-Traversal verhindern (z.B. ../../etc/passwd)
        file_path = (Path(CFG["serve_dir"]) / path).resolve()
        if not str(file_path).startswith(str(Path(CFG["serve_dir"]).resolve())):
            self._send_text(403, "text/plain", b"Forbidden")
            return

        if not file_path.exists() or not file_path.is_file():
            # Binäre Ressourcen (favicon, Bilder) → 404 statt Fallback
            # HTML-Pfade ohne Dateiendung → index.html (Single-Page-App Navigation)
            suffix = file_path.suffix.lower()
            if suffix in ("", ".html", ".htm"):
                file_path = Path(CFG["serve_dir"]) / "frontend" / "index.html"
                if not file_path.exists():
                    file_path = Path(CFG["serve_dir"]) / "index.html"
            else:
                # Ressource nicht gefunden — saubere 404 statt 500
                self._send_text(404, "text/plain",
                    f"404 Not Found: {parsed.path}".encode())
                return

        try:
            content = file_path.read_bytes()
            mime    = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

            log.info("STATIC  %s  [%d bytes]", file_path.name, len(content))

            self.send_response(200)
            self.send_header("Content-Type",   mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control",  "no-cache")
            # CORS-Header auch für statische Dateien — falls der Browser fragt
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)

        except PermissionError:
            self._send_text(403, "text/plain", b"Forbidden")
        except Exception as e:
            log.error("Statik-Fehler: %s", e)
            self._send_text(500, "text/plain", str(e).encode())

    # ── Hilfsmethoden ────────────────────────────────────────────────────────

    def _cors_headers(self):
        """Setzt alle für CORS notwendigen Antwort-Header."""
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")

    def _xml_error(self, code, message):
        """Gibt eine XML-formatierte Fehlermeldung zurück."""
        body = f"<Error><Code>{code}</Code><Message>{message}</Message></Error>".encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)
        log.warning("ERROR %d: %s", code, message)

    def _send_text(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type",   content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# TCP-Server mit Socket-Wiederverwendung
# ---------------------------------------------------------------------------

class ReusingTCPServer(socketserver.TCPServer):
    """
    SO_REUSEADDR: Verhindert "Address already in use"-Fehler wenn der Server
    direkt nach einem Stopp neu gestartet wird (z.B. während der Entwicklung).
    """
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CNC-Dashboard: HTTP-Server + MTConnect CORS-Proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Schnellstart:
  1. Terminal öffnen im dashboard/-Verzeichnis
  2. python cors_proxy.py --mtc-host 148.205.38.221 --mtc-port 8082
  3. Browser öffnen: http://localhost:8888
  4. Im Dashboard-Modal → PROXY URL: http://localhost:8888

Optionen:
  python cors_proxy.py
      Startet auf Port 8888, kein Agent vorkonfiguriert.
      Agent-IP im Dashboard-Modal eingeben.

  python cors_proxy.py --mtc-host 148.205.38.221 --mtc-port 8082
      Agent ist vorkonfiguriert. /proxy/current leitet direkt weiter.

  python cors_proxy.py --port 9000 --mtc-host 148.205.38.221 --mtc-port 8082
      Verwendet Port 9000 statt 8888.

  python cors_proxy.py --dir /pfad/zum/dashboard
      Bedient HTML-Dateien aus einem anderen Verzeichnis.
        """,
    )
    parser.add_argument("--port",     type=int, default=8888,
                        help="Port dieses Servers (Standard: 8888)")
    parser.add_argument("--mtc-host", default=None,
                        help="IP-Adresse des MTConnect-Agents")
    parser.add_argument("--mtc-port", type=int, default=8082,
                        help="Port des MTConnect-Agents (Standard: 8082)")
    parser.add_argument("--dir",      default=None,
                        help="Verzeichnis mit den Dashboard-Dateien (Standard: aktuelles Verzeichnis)")
    args = parser.parse_args()

    CFG["port"]     = args.port
    CFG["mtc_host"] = args.mtc_host
    CFG["mtc_port"] = args.mtc_port

    # ── Projekt-Root und Serve-Verzeichnis bestimmen ─────────────────────
    #
    # Projektstruktur (typisch):
    #   project/
    #   ├── Backend/    ← cors_proxy.py liegt hier
    #   ├── css/        ← styles_iiot.css
    #   ├── frontend/   ← index.html
    #   └── js/         ← app.js, i18n.js
    #
    # Der Proxy bedient Dateien aus dem PROJEKT-ROOT (eine Ebene über Backend/).
    # Damit sind frontend/, css/ und js/ alle über http://localhost:8888/... erreichbar.
    # index.html verweist dann auf:  ../css/styles_iiot.css  und  ../js/app.js
    #
    # Suchreihenfolge für den Projekt-Root:
    #   1. --dir Argument                   (explizit)
    #   2. Elternordner von cors_proxy.py   (Standard: Backend/../  = project/)
    #   3. Gleicher Ordner wie cors_proxy.py
    #   4. Aktuelles Arbeitsverzeichnis

    script_dir  = os.path.dirname(os.path.abspath(__file__))
    parent_dir  = os.path.normpath(os.path.join(script_dir, ".."))
    cwd         = os.getcwd()

    # Projekt-Root = Ordner der index.html via frontend/-Unterordner enthält
    root_candidates = [
        args.dir,
        parent_dir,    # Backend/../  →  project/  (Standardfall)
        script_dir,
        cwd,
    ]

    serve_dir = None
    for c in root_candidates:
        if not c:
            continue
        # index.html direkt im Kandidaten ODER in einem frontend/-Unterordner
        if Path(c, "index.html").exists() or Path(c, "frontend", "index.html").exists():
            serve_dir = c
            break

    if serve_dir is None:
        log.warning("index.html nicht gefunden. Gesucht in:")
        for c in root_candidates:
            if c: log.warning("  %s", c)
        log.warning("Proxy-only Modus. Nutze --dir <Pfad> um Projekt-Root anzugeben.")
        serve_dir = script_dir

    CFG["serve_dir"] = serve_dir

    # index.html Pfad für Healthcheck-Log ermitteln
    if Path(serve_dir, "index.html").exists():
        index_url = "http://localhost:%d/index.html" % CFG["port"]
    elif Path(serve_dir, "frontend", "index.html").exists():
        index_url = "http://localhost:%d/frontend/index.html" % CFG["port"]
    else:
        index_url = "http://localhost:%d" % CFG["port"]

    log.info("=" * 58)
    log.info("  CNC Fräsmaschinen-Dashboard")
    log.info("  Browser öffnen : %s", index_url)
    log.info("  Proxy-URL      : http://localhost:%d", CFG["port"])
    if CFG["mtc_host"]:
        log.info("  MTConnect      : http://%s:%s", CFG["mtc_host"], CFG["mtc_port"])
    else:
        log.info("  MTConnect      : Agent-IP im Dashboard-Modal eingeben")
    log.info("  Projekt-Root   : %s", CFG["serve_dir"])
    log.info("=" * 58)

    with ReusingTCPServer(("", CFG["port"]), DashboardHandler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Server gestoppt (Ctrl+C).")


if __name__ == "__main__":
    main()
