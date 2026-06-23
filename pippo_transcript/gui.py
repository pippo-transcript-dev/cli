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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pippo Transcript</title>
  <style>
    :root { color-scheme: light; --bg:#f4f6f8; --panel:#fff; --line:#d7dde6; --text:#17202a; --muted:#5c6978; --accent:#176b87; --soft:#e8f3f6; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
    header { padding:18px 24px; border-bottom:1px solid var(--line); background:#fbfcfd; display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
    h1 { margin:0; font-size:22px; }
    h2 { margin:0 0 12px; font-size:16px; }
    h3 { margin:18px 0 8px; font-size:15px; }
    p, li { color:var(--muted); line-height:1.5; }
    main { display:grid; grid-template-columns:minmax(380px, 520px) minmax(0, 1fr); gap:18px; padding:18px; max-width:1440px; margin:0 auto; }
    section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }
    label { display:block; font-size:13px; color:var(--muted); margin-bottom:5px; }
    input, select { width:100%; min-height:36px; border:1px solid #bec7d3; border-radius:6px; padding:7px 9px; font:inherit; background:white; color:var(--text); }
    input[type="checkbox"] { width:auto; min-height:auto; margin-right:8px; }
    button { min-height:36px; border:1px solid #aab6c4; border-radius:6px; padding:7px 11px; font:inherit; background:white; color:var(--text); cursor:pointer; }
    button:hover { background:#f0f4f8; }
    button.primary { background:var(--accent); color:white; border-color:var(--accent); font-weight:700; }
    button.primary:hover { background:#12566d; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    code { background:#eef2f6; padding:1px 4px; border-radius:4px; color:#24313d; }
    .tabs { display:flex; gap:8px; }
    .tab.active { background:var(--soft); border-color:#8db9c7; color:#0f5369; font-weight:700; }
    .status { color:var(--muted); font-size:13px; }
    .page { display:none; }
    .page.active { display:grid; }
    .summary { display:grid; gap:10px; margin-bottom:12px; }
    .summary-card { border:1px solid var(--line); border-radius:8px; padding:10px; background:#fbfcfd; }
    .summary-card strong { display:block; font-size:13px; margin-bottom:5px; }
    .path-value { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font-size:13px; min-height:18px; }
    .link-button { min-height:0; border:0; padding:6px 0 0; background:transparent; color:var(--accent); }
    .browser-bar { display:flex; gap:8px; margin-bottom:8px; flex-wrap:wrap; align-items:center; }
    .current-folder { flex:1; min-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font-size:13px; }
    .picker-note { display:none; border:1px solid #b8cbd2; background:#f3fafc; border-radius:8px; padding:10px; margin-bottom:10px; color:#315463; font-size:13px; }
    .picker-note.active { display:block; }
    .list { height:320px; overflow:auto; border:1px solid var(--line); border-radius:6px; background:#fbfcfd; }
    .item { display:grid; grid-template-columns:34px minmax(0,1fr); gap:8px; align-items:center; padding:10px; border-bottom:1px solid #e7ebf0; cursor:pointer; }
    .item:hover { background:#eef4f7; }
    .item-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .item-kind { color:var(--muted); font-size:12px; margin-top:2px; }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .checks { display:grid; gap:8px; margin-top:12px; }
    .check { display:flex; align-items:center; font-size:14px; }
    .actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:14px; }
    .logs { height:calc(100vh - 185px); min-height:460px; background:#101820; color:#e9f1f7; border-radius:6px; padding:12px; overflow:auto; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; white-space:pre-wrap; }
    .links { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    @media (max-width:900px) { main { grid-template-columns:1fr; } .logs { height:380px; min-height:320px; } }
  </style>
</head>
<body>
  <header>
    <h1>Pippo Transcript</h1>
    <div class="tabs">
      <button class="tab active" id="runTab">Run</button>
      <button class="tab" id="helpTab">How it works</button>
    </div>
    <div class="status" id="status">Ready.</div>
  </header>

  <main class="page active" id="runPage">
    <div>
      <section>
        <h2>Choose documents</h2>
        <div class="summary">
          <div class="summary-card">
            <strong>Input</strong>
            <div class="path-value" id="inputLabel">No file or folder selected.</div>
            <input id="inputPath" type="hidden">
          </div>
          <div class="summary-card">
            <strong>Output</strong>
            <div class="path-value" id="outputLabel">Output is set automatically after choosing an input.</div>
            <input id="outputPath" type="hidden">
            <button class="link-button" id="changeOutput">Choose a different output folder</button>
          </div>
        </div>

        <div class="picker-note" id="outputPicker">
          Output picking is active. Navigate to a folder, then click <strong>Use current folder as output</strong>.
          <div class="actions">
            <button id="useCurrentOutput">Use current folder as output</button>
            <button id="cancelOutput">Cancel</button>
          </div>
        </div>

        <div class="browser-bar">
          <button id="parent">Up</button>
          <button id="home">Home</button>
          <button id="refresh">Refresh</button>
          <button id="useCurrentInput">Use this folder as input</button>
          <div class="current-folder" id="currentFolder"></div>
        </div>
        <div class="list" id="fileList"></div>
      </section>

      <section style="margin-top:14px">
        <h2>Settings</h2>
        <div class="grid">
          <div><label for="ocrMode">OCR</label><select id="ocrMode"><option>auto</option><option>always</option><option>never</option></select></div>
          <div><label for="documentType">Document type</label><select id="documentType"><option>classic</option><option>receipt</option><option>business-card</option><option>auto</option></select></div>
          <div><label for="markdownMode">Markdown</label><select id="markdownMode"><option>clean</option><option>audit</option></select></div>
          <div><label for="dpi">DPI</label><input id="dpi" type="number" min="1" value="200"></div>
          <div style="grid-column:1 / -1"><label for="ocrLangs">OCR languages</label><input id="ocrLangs" value="auto"></div>
        </div>
        <div class="checks">
          <label class="check"><input id="includeBlocks" type="checkbox">Include text block coordinates</label>
          <label class="check"><input id="cleanOutput" type="checkbox" checked>Clean each document output before regenerating it</label>
          <label class="check"><input id="skipExisting" type="checkbox">Skip documents that already have complete outputs</label>
        </div>
        <div class="actions">
          <button class="primary" id="run">Run transcription</button>
          <button id="openOutput">Open output folder</button>
          <button id="openIndex">Open report</button>
        </div>
      </section>
    </div>

    <section>
      <h2>Log</h2>
      <div class="logs" id="logs"></div>
      <div class="links" id="links"></div>
    </section>
  </main>

  <main class="page" id="helpPage">
    <section style="grid-column:1 / -1">
      <h2>How it works</h2>
      <p>Pippo Transcript runs locally on your machine. This browser page is only a control panel for the local Python process.</p>

      <h3>Basic flow</h3>
      <ol>
        <li>Browse to the folder that contains your PDFs or images.</li>
        <li>Click a supported file to process one document, or click <strong>Use this folder as input</strong> to process the whole folder recursively.</li>
        <li>The output folder is created automatically next to the selected input.</li>
        <li>Change settings only when needed, then click <strong>Run transcription</strong>.</li>
        <li>Open the generated HTML report or output folder when the run is complete.</li>
      </ol>

      <h3>Output</h3>
      <p>Each processed document can produce Markdown, HTML, JSON, plain text, rendered page images, table crops, visual crops, and extracted images. When the input is a folder, an <code>index.html</code> report is created at the root of the output folder.</p>

      <h3>Settings</h3>
      <ul>
        <li><strong>OCR auto</strong> uses native PDF text when available and OCR when needed.</li>
        <li><strong>OCR always</strong> forces OCR. This is useful for scans, but slower.</li>
        <li><strong>OCR never</strong> avoids Tesseract and only uses native text or image extraction.</li>
        <li><strong>Markdown clean</strong> is the normal readable output.</li>
        <li><strong>Markdown audit</strong> includes more technical details for inspection.</li>
      </ul>

      <h3>Supported files</h3>
      <p>PDF, PNG, JPG, JPEG, TIFF, WEBP, and BMP files are shown in the browser.</p>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    let currentPath = "";
    let choosingOutput = false;

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

    function showPage(name) {
      const isRun = name === "run";
      $("runPage").classList.toggle("active", isRun);
      $("helpPage").classList.toggle("active", !isRun);
      $("runTab").classList.toggle("active", isRun);
      $("helpTab").classList.toggle("active", !isRun);
    }

    function defaultOutputFor(path, kind) {
      if (!path) return "";
      const parent = kind === "dir" ? path : path.split("/").slice(0, -1).join("/");
      return `${parent}/pippo-transcripted-files`;
    }

    function setOutputPath(path) {
      $("outputPath").value = path;
      $("outputLabel").textContent = path || "No output folder selected.";
    }

    function setInputPath(path, kind) {
      $("inputPath").value = path;
      $("inputLabel").textContent = path;
      setOutputPath(defaultOutputFor(path, kind));
      choosingOutput = false;
      $("outputPicker").classList.remove("active");
    }

    async function browse(path) {
      const data = await api(`/api/list?path=${encodeURIComponent(path || "")}`);
      currentPath = data.path;
      $("currentFolder").textContent = data.path;
      const list = $("fileList");
      list.innerHTML = "";
      data.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "item";
        const icon = document.createElement("span");
        icon.textContent = item.kind === "dir" ? "[D]" : "[F]";
        const text = document.createElement("div");
        const name = document.createElement("div");
        name.className = "item-name";
        name.textContent = item.name;
        const hint = document.createElement("div");
        hint.className = "item-kind";

        if (item.kind === "dir") {
          hint.textContent = choosingOutput ? "Click to choose this folder as output. Double-click to open it." : "Click to open this folder.";
          row.onclick = () => choosingOutput ? setOutputPath(item.path) : browse(item.path);
          row.ondblclick = () => browse(item.path);
        } else {
          hint.textContent = "Click to select this file as input.";
          row.onclick = () => setInputPath(item.path, item.kind);
        }

        text.append(name, hint);
        row.append(icon, text);
        list.appendChild(row);
      });
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
      setLogs(["Starting..."]);
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
        setStatus(data.running ? "Transcription running..." : data.message);
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
        button.textContent = `Report: ${result.name}`;
        button.onclick = () => openPath(result.html);
        links.appendChild(button);
      });
    }

    async function openPath(path) {
      if (!path) return;
      await api(`/api/open?path=${encodeURIComponent(path)}`);
    }

    $("runTab").onclick = () => showPage("run");
    $("helpTab").onclick = () => showPage("help");
    $("parent").onclick = () => browse(`${currentPath}/..`);
    $("home").onclick = () => browse("~");
    $("refresh").onclick = () => browse(currentPath);
    $("useCurrentInput").onclick = () => setInputPath(currentPath, "dir");
    $("changeOutput").onclick = () => {
      choosingOutput = true;
      $("outputPicker").classList.add("active");
      browse(currentPath);
    };
    $("useCurrentOutput").onclick = () => {
      setOutputPath(currentPath);
      choosingOutput = false;
      $("outputPicker").classList.remove("active");
      browse(currentPath);
    };
    $("cancelOutput").onclick = () => {
      choosingOutput = false;
      $("outputPicker").classList.remove("active");
      browse(currentPath);
    };
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


def translate_log_message(message):
    text = str(message)
    replacements = {
        "Aucun fichier PDF/image supporte trouve.": "No supported PDF/image file found.",
        "Aucun fichier PDF/image supporté trouvé.": "No supported PDF/image file found.",
        "fichier(s) a traiter.": "file(s) to process.",
        "fichier(s) à traiter.": "file(s) to process.",
        "deja traite, ignore": "already processed, skipped",
        "déjà traité, ignoré": "already processed, skipped",
        "Index HTML": "HTML index",
        "Termine.": "Done.",
        "Terminé.": "Done.",
        "Erreur:": "Error:",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.message = "Ready."
        self.logs = []
        self.results = []
        self.output_path = None

    def append(self, message):
        with self.lock:
            self.logs.append(translate_log_message(message))

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
            STATE.message = f"Failed: {exc}"
            STATE.running = False
        STATE.append(STATE.message)
        return

    with STATE.lock:
        STATE.results = [result_payload(result) for result in results]
        STATE.message = "Transcription complete."
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
            self.send_json({"error": "Folder not found."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            children = sorted(path.iterdir(), key=lambda child: (child.is_file(), child.name.lower()))
        except PermissionError:
            self.send_json({"error": "Permission denied."}, HTTPStatus.FORBIDDEN)
            return

        items = []
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                items.append({"name": child.name, "path": str(child), "kind": "dir"})
            elif child.suffix.lower() in SUPPORTED_EXTENSIONS:
                items.append({"name": child.name, "path": str(child), "kind": "file"})

        self.send_json({
            "path": str(path),
            "items": items,
        })

    def handle_open(self, parsed):
        requested = parse_qs(parsed.query).get("path", [""])[0]
        path = expand_path(requested)
        if not path.exists():
            self.send_json({"error": "Path not found."}, HTTPStatus.BAD_REQUEST)
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
            self.send_json({"error": "Invalid settings."}, HTTPStatus.BAD_REQUEST)
            return

        if not input_path.exists():
            self.send_json({"error": "Input not found."}, HTTPStatus.BAD_REQUEST)
            return
        if not output_path:
            self.send_json({"error": "Output folder is missing."}, HTTPStatus.BAD_REQUEST)
            return
        if dpi <= 0:
            self.send_json({"error": "DPI must be greater than 0."}, HTTPStatus.BAD_REQUEST)
            return

        with STATE.lock:
            if STATE.running:
                self.send_json({"error": "A transcription is already running."}, HTTPStatus.CONFLICT)
                return
            STATE.running = True
            STATE.message = "Transcription running..."
            STATE.logs = [f"Input: {input_path}", f"Output: {output_path}"]
            STATE.results = []
            STATE.output_path = output_path

        thread = threading.Thread(target=run_worker, args=(config,), daemon=True)
        thread.start()
        self.send_json({"ok": True, "message": "Transcription started."})

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
    parser = argparse.ArgumentParser(description="Local web interface for Pippo Transcript.")
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
