"""EXPLICIT TEST HARNESS: a local stand-in for the Gemini chat web app.

It reproduces only the DOM contract the CDP driver relies on (rich-textarea,
<input type="file" accept="image/*">, attachment chips, "Send message",
user-query / model-response, generated-image thumbnails served at "=s512",
full-size originals at "=s0", and "Download full size image" buttons).
It never contacts Google and is never imported by the production engine.
"""
from __future__ import annotations

import io
import json
from email.parser import BytesParser
from email.policy import default as email_policy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image

COLOURS = [(220, 40, 40), (40, 160, 70), (40, 80, 220), (230, 180, 30)]
THUMB = (512, 640)
FULL = (1792, 2240)


def png(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Gemini</title></head><body>
<header>%(account)s</header>
<main id="chat"></main>
<input-area-v2>
  <div id="chips"></div>
  <rich-textarea><div class="ql-editor" contenteditable="true" role="textbox" aria-label="Enter a prompt here"></div></rich-textarea>
  <button aria-label="Open upload file menu" id="upload">+</button>
  <div id="menu"></div>
  %(file_input)s
  <button aria-label="Send message" id="send" disabled>send</button>
</input-area-v2>
<script>
const CFG = %(cfg)s;
const files = []; let busy = false, turn = 0;
const editor = document.querySelector('.ql-editor'), send = document.getElementById('send');
setInterval(() => { send.disabled = !editor.innerText.trim() || busy; }, 80);
function addFiles(list) {
  busy = true;
  const bar = document.createElement('div'); bar.setAttribute('role', 'progressbar'); document.getElementById('chips').appendChild(bar);
  for (const f of list) {
    files.push(f);
    const card = document.createElement('div'); card.className = 'file-preview-card';
    const img = document.createElement('img'); img.src = URL.createObjectURL(f); card.appendChild(img);
    document.getElementById('chips').appendChild(card);
  }
  setTimeout(() => { bar.remove(); busy = false; }, 300);
}
function wire(input) { input.addEventListener('change', () => addFiles(input.files)); }
const staticInput = document.getElementById('file'); if (staticInput) wire(staticInput);
document.getElementById('upload').addEventListener('click', () => {
  const item = document.createElement('button'); item.setAttribute('role', 'menuitem'); item.textContent = 'Upload files';
  item.addEventListener('click', () => {
    const input = document.createElement('input'); input.type = 'file'; input.accept = 'image/*'; input.multiple = true;
    wire(input); input.click();
  });
  document.getElementById('menu').replaceChildren(item);
});
send.addEventListener('click', async () => {
  const text = editor.innerText;
  const q = document.createElement('user-query'); q.textContent = text;
  for (const f of files) { const i = document.createElement('img'); i.src = URL.createObjectURL(f); q.appendChild(i); }
  document.getElementById('chat').appendChild(q);
  const fd = new FormData(); fd.append('prompt', text); files.forEach((f, n) => fd.append('file' + n, f, f.name));
  await fetch('/submit', { method: 'POST', body: fd });
  editor.innerHTML = ''; files.length = 0; document.getElementById('chips').replaceChildren();
  const stop = document.createElement('button'); stop.setAttribute('aria-label', 'Stop response'); document.body.appendChild(stop);
  turn += 1;
  setTimeout(() => {
    const r = document.createElement('model-response');
    if (CFG.reply === 'refuse') { r.textContent = "I can't generate that image. Try a different prompt."; }
    else if (CFG.reply === 'quota') { r.textContent = "You've reached your limit for generating images. Try again tomorrow."; }
    else {
      r.appendChild(document.createTextNode('Here is your image.'));
      for (let k = 1; k <= CFG.images; k++) {
        const g = document.createElement('generated-image');
        const img = document.createElement('img'); img.src = '/img/' + turn + '-' + k + '=s512'; g.appendChild(img);
        if (CFG.download) {
          const b = document.createElement('button'); b.setAttribute('aria-label', 'Download full size image');
          b.addEventListener('click', () => { const a = document.createElement('a'); a.href = '/img/' + turn + '-' + k + '=s0?dl=1'; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); });
          g.appendChild(b);
        }
        r.appendChild(g);
      }
    }
    document.getElementById('chat').appendChild(r);
    stop.remove();
  }, CFG.delayMs);
});
</script></body></html>"""


class FakeGemini:
    def __init__(self) -> None:
        self.mode = "input"        # "input" = static <input type=file>; "chooser" = menu → file chooser
        self.reply = "image"       # image | refuse | quota
        self.images = 1            # generated images per response (to prove only ONE is kept)
        self.download = True       # render "Download full size image" buttons
        self.delay_ms = 1200
        self.submissions: list[dict] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.app_url = f"http://127.0.0.1:{self.port}/app"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path = urlparse(self.path).path
                if path == "/login":  # what the human's manual sign-in achieves: a persistent session cookie
                    self.send_response(302)
                    self.send_header("Set-Cookie", "SID=fixture-session; Path=/; Max-Age=86400; HttpOnly")
                    self.send_header("Location", "/app")
                    self.end_headers()
                    return
                if path == "/app" or path.startswith("/app/"):
                    signed = "SID=fixture-session" in (self.headers.get("Cookie") or "")
                    account = ('<a href="#" aria-label="Google Account: Fixture User (fixture.user@gmail.com)">FU</a>'
                               if signed else '<a href="/login">Sign in</a>')
                    file_input = '<input type="file" accept="image/*" multiple hidden id="file">' if fake.mode == "input" else ""
                    cfg = json.dumps({"reply": fake.reply, "images": fake.images, "download": fake.download, "delayMs": fake.delay_ms})
                    body = PAGE % {"account": account, "file_input": file_input, "cfg": cfg}
                    self._send(200, body.encode(), "text/html; charset=utf-8")
                    return
                if path.startswith("/img/"):
                    name, _, size = path[5:].partition("=")
                    k = int(name.split("-")[1]) - 1
                    full = size.startswith("s0")
                    data = png(FULL if full else THUMB, COLOURS[k % len(COLOURS)])
                    headers = {"Content-Disposition": f'attachment; filename="gemini_{name}.png"'} if "dl=1" in self.path else {}
                    self._send(200, data, "image/png", headers)
                    return
                self._send(404, b"not found", "text/plain")

            def do_POST(self):  # noqa: N802
                if urlparse(self.path).path != "/submit":
                    self._send(404, b"", "text/plain")
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                head = f"Content-Type: {self.headers.get('Content-Type')}\r\n\r\n".encode()
                message = BytesParser(policy=email_policy).parsebytes(head + body)
                prompt, files = "", []
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if name == "prompt":
                        prompt = part.get_content()
                    elif name and name.startswith("file"):
                        files.append((part.get_filename(), part.get_payload(decode=True)))
                fake.submissions.append({"prompt": prompt, "files": files})
                self._send(200, b"{}", "application/json")

        return Handler
