# -*- coding: utf-8 -*-
"""A local HTTP server that stands in for the providers and for NeoMundi in the tests.
It records every request byte for byte and answers from scripted responses."""
import json, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


class MockServer:
    def __init__(self):
        self.requests = []
        self.routes = {}
        self.lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                with mock.lock:
                    mock.requests.append({"path": self.path, "body": body,
                                          "headers": {k.lower(): v for k, v in self.headers.items()}})
                    status, headers, out = mock._respond(self.path, body)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def route(self, prefix, responder):
        """responder: a (status, headers, bytes) tuple, a list of them used in order (the
        last one repeats), or a function (path, body) -> tuple."""
        self.routes[prefix] = responder

    def _respond(self, path, body):
        best = max((p for p in self.routes if path.startswith(p)), key=len, default=None)
        if best is None:
            return 404, {}, b'{"error": "no route"}'
        r = self.routes[best]
        if callable(r):
            return r(path, body)
        if isinstance(r, list):
            return r.pop(0) if len(r) > 1 else r[0]
        return r

    def to(self, prefix):
        return [q for q in self.requests if q["path"].startswith(prefix)]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ok(obj, headers=None):
    body = obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")
    return 200, dict({"Content-Type": "application/json"}, **(headers or {})), body


def openai_body(content="ok", tool_calls=None, model="oa-model", usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "chatcmpl-TEST1234567890", "object": "chat.completion", "created": 1,
            "model": model,
            "choices": [{"index": 0, "message": msg,
                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10,
                               "prompt_tokens_details": {"cached_tokens": 40}}}
