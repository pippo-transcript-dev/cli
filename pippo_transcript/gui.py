import argparse
import json
import socket
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cli import run_transcription
from .core import SUPPORTED_EXTENSIONS


APP_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pippo Transcript</title>
  <style>
    :root { color-scheme: light; --bg:#f4f6f8; --panel:#ffffff; --line:#d7dde6; --text:#17202a; --muted:#5c6978; --accent:#176b87; --accent-2:#c2410c; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
    header { padding:18px 24px; border-bottom:1px solid var(--line); background:#fbfcfd; display:flex; align-items:center; justify-content:space-between; gap:16px; }
    h1 { margin:0; font-size:22px; font-weight:700; }
    main { display:grid; grid-template-columns:minmax(360px, 470px) minmax(0, 1fr); gap:18px; padding:18px; max-width:1440px; margin:0 auto; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    h2 { margin:0 0 12px; font-size:16px; }
    label { display:block; font-size:13px; color:var(--muted); margin-bottom:5px; }
    input, select { width:100%; min-height:36px; border:1px solid #bec7d3; border-radius:6px; padding:7px 9px; font:inherit; background:white; color:var(--text); }
    input[type="checkbox"] { width:auto; min-height:auto; margin-right:8px; }
    button { min-height:36px; border:1px solid #aab6c4; border-radius:6px; padding:7px 11px; font:inherit; background:white; color:var(--text); cursor:pointer; }
    button:hover { background:#f0f4f8; }
    button.primary { background:var(--accent); color:white; border-color:var(--accent); font-weight:700; }
    button.primary:hover { background:#12566d; }
    button.warn { color:var(--accent-2); }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .row { display:flex; gap:8px; align-items:end; }
    .row > div { flex:1; min-width:0; }
    .checks { display:grid; grid-template-columns:1fr; gap:8px; margin-top:12px; }
    .check { display:flex; align-items:center; color:var(--text); font-size:14px; }
    .actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:14px; }
    .status { color:var(--muted); font-size:13px; }
    .browser { margin-top:12px; }
    .browser-bar { display:flex; gap:8px; margin-bottom:8px; }
    .list { height:280px; overflow:auto; border:1px solid var(--line); border-radius:6px; background:#fbfcfd; }
    .item { display:grid; grid-template-columns:26px minmax(0, 1fr) auto; gap:8px; align-items:center; padding:7px 9px; border-bottom:1px solid #e7ebf0; }
    .item:hover { background:#eef4f7; }
    .item-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .item-actions { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
    .item-actions button { min-height:30px; padding:4px 8px; font-size:12px; }
    .item small { color:var(--muted); }
    .logs { height:calc(100vh - 185px); min-height:460px; background:#101820; color:#e9f1f7; border-radius:6px; padding:12px; overflow:auto; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; white-space:pre-wrap; }
    .links { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    @media (max-width: 900px) { main { grid-template-columns:1fr; } .logs { height:380px; min-height:320px; } }
  </style>
</head>
<body>
  <header>
    <h1>Pippo Transcript</h1>
    <div class="status" id="status">Prêt.</div>
  </header>
  <main>
    <div>
      <section>
        <h2>Entrée et sortie</h2>
        <div class="row">
          <div>
            <label for="inputPath">Fichier ou dossier</label>
            <input id="inputPath" autocomplete="off">
          </div>
          <button id="useCurrent">Dossier affiché</button>
        </div>
        <div class="row" style="margin-top:10px">
          <div>
            <label for="outputPath">Dossier de sortie</label>
            <input id="outputPath" autocomplete="off">
          </div>
          <button id="useOutput">Sortie</button>
        </div>
        <div class="browser">
          <div class="browser-bar">
            <button id="parent">Parent</button>
            <button id="refresh">Actualiser</button>
            <button id="home">Accueil</button>
          </div>
          <div class="list" id="fileList"></div>
        </div>
      </section>
      <section style="margin-top:14px">
        <h2>Paramètres</h2>
        <div class="grid">
          <div><label for="ocrMode">OCR</label><select id="ocrMode"><option>auto</option><option>always</option><option>never</option></select></div>
          <div><label for="documentType">Type</label><select id="documentType"><option>classic</option><option>receipt</option><option>business-card</option><option>auto</option></select></div>
          <div><label for="markdownMode">Markdown</label><select id="markdownMode"><option>clean</option><option>audit</option><option>bki-tables</option></select></div>
          <div><label for="dpi">DPI</label><input id="dpi" type="number" min="1" value="200"></div>
          <div style="grid-column:1 / -1"><label for="ocrLangs">Langues OCR</label><input id="ocrLangs" value="auto"></div>
        </div>
        <div class="checks">
          <label class="check"><input id="includeBlocks" type="checkbox">Inclure les blocs texte</label>
          <label class="check"><input id="cleanOutput" type="checkbox" checked>Nettoyer la sortie</label>
          <label class="check"><input id="skipExisting" type="checkbox">Ignorer les fichiers déjà traités</label>
        </div>
        <div class="actions">
          <button class="primary" id="run">Exécuter</button>
          <button id="openOutput">Ouvrir la sortie</button>
          <button id="openIndex">Ouvrir le rapport</button>
        </div>
      </section>
    </div>
    <section>
      <h2>Journal</h2>
      <div class="logs" id="logs"></div>
      <div class="links" id="links"></div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let currentPath = "";
    let lastStatus = null;

    async function api(path, options = {}) {
      const response = await fetch(path, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || response.statusText);
      return data;
    }

    function setStatus(text) { $("status").textContent = text; }
    function setLogs(lines) {
      $("logs").textContent = lines.join("\\n");
      $("logs").scrollTop = $("logs").scrollHeight;
    }

    async function browse(path) {
      const data = await api(`/api/list?path=${encodeURIComponent(path || "")}`);
      currentPath = data.path;
      $("inputPath").value = $("inputPath").value || data.path;
      $("outputPath").value = $("outputPath").value || data.default_output;
      const list = $("fileList");
      list.innerHTML = "";
      data.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "item";
        const icon = document.createElement("span");
        icon.textContent = item.kind === "dir" ? "▸" : "•";
        const name = document.createElement("span");
        name.className = "item-name";
        name.textContent = item.name;
        const actions = document.createElement("div");
        actions.className = "item-actions";

        if (item.kind === "dir") {
          const open = document.createElement("button");
          open.textContent = "Ouvrir";
          open.onclick = () => browse(item.path);
          const input = document.createElement("button");
          input.textContent = "Entrée";
          input.onclick = () => setInputPath(item.path, item.kind);
          const output = document.createElement("button");
          output.textContent = "Sortie";
          output.onclick = () => $("outputPath").value = item.path;
          actions.append(open, input, output);
          name.ondblclick = () => browse(item.path);
        } else {
          const input = document.createElement("button");
          input.textContent = "Entrée";
          input.onclick = () => setInputPath(item.path, item.kind);
          actions.append(input);
          name.ondblclick = () => setInputPath(item.path, item.kind);
        }

        row.append(icon, name, actions);
        list.appendChild(row);
      });
    }

    function setInputPath(path, kind) {
      $("inputPath").value = path;
      if (!$("outputPath").value || $("outputPath").value.endsWith("/pippo-transcripted-files")) {
        const parent = kind === "dir" ? path : path.split("/").slice(0, -1).join("/");
        $("outputPath").value = `${parent}/pippo-transcripted-files`;
      }
    }

    function payload() {
      return {
        input_path: $("inputPath").value,
        output_path: $("outputPath").value,
        dpi: Number($("dpi").value),
        ocr_mode: $("ocrMode").value,
        ocr_langs: $("ocrLangs").value,
        document_type: $("documentType").value,
        markdown_mode: $("markdownMode").value,
        include_blocks: $("includeBlocks").checked,
        clean_output: $("cleanOutput").checked,
        skip_existing: $("skipExisting").checked
      };
    }

    async function startRun() {
      $("run").disabled = true;
      $("links").innerHTML = "";
      setLogs(["Lancement..."]);
      try {
        const data = await api("/api/start", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload())
        });
        setStatus(data.message);
      } catch (error) {
        setStatus(error.message);
        $("run").disabled = false;
      }
    }

    async function pollStatus() {
      try {
        const data = await api("/api/status");
        lastStatus = data;
        setStatus(data.running ? "Transcription en cours..." : data.message);
        setLogs(data.logs);
        $("run").disabled = data.running;
        renderLinks(data);
      } catch (error) {
        setStatus(error.message);
      } finally {
        setTimeout(pollStatus, 1000);
      }
    }

    function renderLinks(data) {
      const links = $("links");
      links.innerHTML = "";
      (data.results || []).forEach((result) => {
        if (!result.html) return;
        const button = document.createElement("button");
        button.textContent = `Rapport: ${result.name}`;
        button.onclick = () => openPath(result.html);
        links.appendChild(button);
      });
    }

    async function openPath(path) {
      if (!path) return;
      await api(`/api/open?path=${encodeURIComponent(path)}`);
    }

    $("useCurrent").onclick = () => { $("inputPath").value = currentPath; };
    $("useOutput").onclick = () => { $("outputPath").value = currentPath; };
    $("parent").onclick = () => browse(`${currentPath}/..`);
    $("refresh").onclick = () => browse(currentPath);
    $("home").onclick = () => browse("~");
    $("run").onclick = startRun;
    $("openOutput").onclick = () => openPath($("outputPath").value);
    $("openIndex").onclick = () => {
      const out = $("outputPath").value.replace(/\\/$/, "");
      openPath(`${out}/index.html`);
    };

    browse("").then(pollStatus).catch((error) => setStatus(error.message));
  </script>
</body>
</html>
"""


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.message = "Prêt."
        self.logs = []
        self.results = []
        self.output_path = None

    def append(self, message):
        with self.lock:
            self.logs.append(str(message))

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "message": self.message,
                "logs": list(self.logs),
                "results": list(self.results),
                "output_path": str(self.output_path) if self.output_path else "",
            }


STATE = AppState()


def expand_path(value):
    if not value:
        return Path.cwd()
    return Path(value).expanduser().resolve()


def result_payload(result):
    return {
        "name": Path(result["source"]).name,
        "source": str(result["source"]),
        "out_dir": str(result["out_dir"]),
        "html": str(result["html"]) if result.get("html") else "",
        "markdown": str(result["markdown"]) if result.get("markdown") else "",
        "json": str(result["json"]) if result.get("json") else "",
        "text": str(result["text"]) if result.get("text") else "",
        "status": result.get("status", "ok"),
        "error": result.get("error", ""),
    }


def run_worker(config):
    input_path = expand_path(config["input_path"])
    output_path = expand_path(config["output_path"])
    try:
        results = run_transcription(
            input_path,
            output_path,
            dpi=int(config.get("dpi") or 200),
            ocr_mode=config.get("ocr_mode") or "auto",
            ocr_langs=(config.get("ocr_langs") or "auto").strip() or "auto",
            document_type=config.get("document_type") or "classic",
            include_blocks=bool(config.get("include_blocks")),
            markdown_mode=config.get("markdown_mode") or "clean",
            clean=bool(config.get("clean_output")),
            skip_existing=bool(config.get("skip_existing")),
            log=STATE.append,
            error_log=STATE.append,
        )
    except BaseException as exc:
        with STATE.lock:
            STATE.message = f"Échec: {exc}"
            STATE.running = False
        STATE.append(STATE.message)
        return

    with STATE.lock:
        STATE.results = [result_payload(result) for result in results]
        STATE.message = "Transcription terminée."
        STATE.running = False
        STATE.output_path = output_path


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(APP_HTML)
        elif parsed.path == "/api/list":
            self.handle_list(parsed)
        elif parsed.path == "/api/status":
            self.send_json(STATE.snapshot())
        elif parsed.path == "/api/open":
            self.handle_open(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if urlparse(self.path).path == "/api/start":
            self.handle_start()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        return

    def handle_list(self, parsed):
        requested = parse_qs(parsed.query).get("path", [""])[0]
        path = Path.home() if requested == "~" else expand_path(requested)
        if path.is_file():
            path = path.parent
        if not path.exists():
            self.send_json({"error": "Dossier introuvable."}, HTTPStatus.BAD_REQUEST)
            return

        items = []
        try:
            children = sorted(path.iterdir(), key=lambda child: (child.is_file(), child.name.lower()))
        except PermissionError:
            self.send_json({"error": "Permission refusée."}, HTTPStatus.FORBIDDEN)
            return

        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                items.append({"name": child.name, "path": str(child), "kind": "dir"})
            elif child.suffix.lower() in SUPPORTED_EXTENSIONS:
                items.append({"name": child.name, "path": str(child), "kind": "file"})

        self.send_json({
            "path": str(path),
            "default_output": str(path / "pippo-transcripted-files"),
            "items": items,
        })

    def handle_open(self, parsed):
        requested = parse_qs(parsed.query).get("path", [""])[0]
        path = expand_path(requested)
        if not path.exists():
            self.send_json({"error": "Chemin introuvable."}, HTTPStatus.BAD_REQUEST)
            return
        webbrowser.open(path.as_uri())
        self.send_json({"ok": True})

    def handle_start(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            config = json.loads(self.rfile.read(length).decode("utf-8"))
            input_path = expand_path(config.get("input_path", ""))
            output_path = expand_path(config.get("output_path", ""))
            dpi = int(config.get("dpi") or 0)
        except Exception:
            self.send_json({"error": "Paramètres invalides."}, HTTPStatus.BAD_REQUEST)
            return

        if not input_path.exists():
            self.send_json({"error": "Entrée introuvable."}, HTTPStatus.BAD_REQUEST)
            return
        if dpi <= 0:
            self.send_json({"error": "Le DPI doit être supérieur à 0."}, HTTPStatus.BAD_REQUEST)
            return

        with STATE.lock:
            if STATE.running:
                self.send_json({"error": "Une transcription est déjà en cours."}, HTTPStatus.CONFLICT)
                return
            STATE.running = True
            STATE.message = "Transcription en cours..."
            STATE.logs = [f"Entrée: {input_path}", f"Sortie: {output_path}"]
            STATE.results = []
            STATE.output_path = output_path

        thread = threading.Thread(target=run_worker, args=(config,), daemon=True)
        thread.start()
        self.send_json({"ok": True, "message": "Transcription lancée."})

    def send_html(self, content):
        body = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def available_port(preferred):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description="Interface web locale pour Pippo Transcript.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    port = available_port(args.port)
    server = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://{args.host}:{port}/"
    print(f"Pippo Transcript UI: {url}")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
