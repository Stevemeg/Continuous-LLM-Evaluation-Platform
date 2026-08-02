"""An OpenAI-compatible endpoint that fails on demand.

ADR-003 requires the four failure modes in `REQ-N-REL-4` to be induced
deliberately, including malformed responses. A real provider cannot be asked to
return malformed JSON, so the spike needs an endpoint it controls. This is that
endpoint, and nothing more: it is a fault injector, not an inference engine. The
genuinely self-hosted endpoint in this spike is Ollama, which is a separate leg.

Modes, selected by the model name in the request:
  ok            - a well-formed response with usage
  ratelimit     - HTTP 429 with the provider-shaped rate-limit error body
  malformed     - HTTP 200 whose body is not a valid completion
  deprecated    - HTTP 404 model_not_found
  (outage is induced by stopping this server, not by a mode)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, payload, raw=False):
        body = payload if raw else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "spike"}
                for m in ("ok", "ratelimit", "malformed", "deprecated")]})
        else:
            self._send(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        model = req.get("model", "ok")

        if model == "ratelimit":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "2")
            body = json.dumps({"error": {
                "message": "Rate limit reached for requests",
                "type": "rate_limit_error", "code": "rate_limit_exceeded"}}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if model == "deprecated":
            self._send(404, {"error": {
                "message": "The model `deprecated` has been deprecated and is no "
                           "longer available.",
                "type": "invalid_request_error", "code": "model_not_found"}})
            return

        if model == "malformed":
            # HTTP 200, Content-Type says JSON, body is not a completion.
            self._send(200, b'{"choices": [ {"mesage": ', raw=True)
            return

        prompt = req.get("messages", [{}])[-1].get("content", "")
        self._send(200, {
            "id": "chatcmpl-spike", "object": "chat.completion", "created": 0,
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "OK"}}],
            "usage": {"prompt_tokens": max(1, len(prompt) // 4),
                      "completion_tokens": 1,
                      "total_tokens": max(1, len(prompt) // 4) + 1},
        })


if __name__ == "__main__":
    print(f"stub provider on :{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
