"""A tiny OpenAI-compatible stub used ONLY for tests and offline demos of the SLM integration.

It is not a language model: it returns canned answers so we can verify how CareFlow treats
SLM output (quote verification, raise-only, no-diagnosis filter). For real use, point
CAREFLOW_SLM_URL at Ollama / llama.cpp / vLLM serving a <20B instruct model.

  python -m scripts.mock_slm_server --port 8799
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

CANNED = [
    # (pattern in patient data, concept, quote) - includes one deliberately hallucinated quote
    (r"elephant", "chest_pain", "an elephant is sitting on me"),
    (r"elephant", "sweating", "drenched in sweat"),  # not in the text -> must be rejected
]


def respond(messages: list[dict]) -> dict:
    user = messages[-1]["content"]
    if "Translate the patient's words" in user:
        data = re.search(r"<patient_data>(.*)</patient_data>", user, re.S).group(1)
        return {"english": f"[mock translation] {data.strip()}"}
    if "Identify symptoms" in user:
        signals = []
        for line in re.findall(r"^\[(S[\d.]+)\] (.*)$", user, re.M):
            seg, text = line
            for pat, concept, quote in CANNED:
                if re.search(pat, text, re.I):
                    signals.append({"concept": concept, "segment": seg, "quote": quote})
        return {"signals": signals}
    if "ONE neutral sentence" in user:
        if "chest pain" in user:
            return {"summary": "Patient likely has a heart attack."}  # diagnostic -> must be discarded
        return {"summary": "Patient reports the listed concerns."}
    return {}


class H(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = {"choices": [{"message": {"role": "assistant", "content": json.dumps(respond(body["messages"]))}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def serve(port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), H)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    print(f"mock SLM on http://127.0.0.1:{ap.parse_args().port}/v1")
    serve(ap.parse_args().port).serve_forever()
