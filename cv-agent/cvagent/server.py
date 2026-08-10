"""Local web app for Stage 1.

Serves the paste-a-listing UI and proxies the Claude call. The original artifact
called the Anthropic API straight from the browser, which put Zac's API key in
page source; here the key stays in the server process and never reaches the client.

    export ANTHROPIC_API_KEY=sk-ant-...
    python -m cvagent.server
"""

from __future__ import annotations

import json
import os
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bank import load_bank
from .render import CSS, render_cv_body, render_cv_page, render_cv_text
from .tailor import tailor_cv

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY_BYTES = 256 * 1024


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        print(f"  {self.command} {self.path}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if self.path != "/api/tailor":
            self._send_json(404, {"error": "unknown endpoint"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "job listing too long"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            bank = load_bank()
            result = tailor_cv(payload.get("listing", ""), payload.get("hint", "auto"), bank=bank)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:  # surface the real cause in the UI, not a blank box
            traceback.print_exc()
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return

        response = result.to_dict()
        response["html"] = render_cv_body(result.cv, bank)
        response["css"] = CSS
        response["page"] = render_cv_page(result.cv, bank)
        response["text"] = render_cv_text(result.cv, bank)
        self._send_json(200, response)


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("warning: ANTHROPIC_API_KEY is not set — generation will fail until it is.")
    port = int(os.environ.get("CV_AGENT_PORT", "8000"))
    print(f"CV tailoring agent: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
