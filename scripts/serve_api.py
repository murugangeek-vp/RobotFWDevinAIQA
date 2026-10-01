"""Local REST stub for PROD-03 — serves the sample CSV as paginated JSON.

    GET /customers?page=1&page_size=25  ->  {"rows": [{...}, ...]}
    GET /customers_flaky                ->  500 twice, then behaves like /customers
                                           (proves retry/backoff)

Test support only — not for production use.
"""

import argparse
import csv
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    rows: list = []
    flaky_hits = 0

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/customers_flaky":
            Handler.flaky_hits += 1
            if Handler.flaky_hits <= 2:
                self._json(500, {"error": "transient failure"})
                return
        if parsed.path not in ("/customers", "/customers_flaky"):
            self._json(404, {"error": "not found"})
            return
        q = parse_qs(parsed.query)
        page = int(q.get("page", ["1"])[0])
        size = int(q.get("page_size", ["25"])[0])
        chunk = self.rows[(page - 1) * size : page * size]
        self._json(200, {"rows": chunk, "page": page})

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--csv", default=str(ROOT / "data" / "samples" / "customer.csv"))
    args = ap.parse_args()
    with open(args.csv, newline="", encoding="utf-8") as f:
        Handler.rows = list(csv.DictReader(f))
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
