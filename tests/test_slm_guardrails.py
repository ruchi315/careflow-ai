import threading

from app import config
from app.pipeline import orchestrator
from scripts.mock_slm_server import serve


def test_slm_can_add_quoted_signal_but_not_hallucinate_or_diagnose(monkeypatch):
    srv = serve(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "SLM_URL", f"http://127.0.0.1:{port}/v1")
    try:
        case = {"code": "T", "patient": {"name": "V", "age": 61, "sex": "M"}, "language": "en", "vitals": {},
                "created_at": "2026-10-19T10:00:00"}
        text = "It feels like an elephant is sitting on me, started 40 minutes ago, and I feel sick."
        a = orchestrator.run(case, [{"id": 1, "type": "text", "content": text}])
        # Quoted signal accepted -> chest pain + nausea -> P1 via rules
        assert a["priority"]["level"] == "P1"
        assert a["facts"]["symptoms"]["chest_pain"]["origins"] == ["slm"]
        # Hallucinated quote ("drenched in sweat") rejected
        assert "sweating" not in a["facts"]["symptoms"]
        # Diagnostic summary discarded
        assert "summary_slm" not in a or "heart attack" not in a["summary_slm"]
    finally:
        srv.shutdown()


def test_slm_never_lowers_priority(monkeypatch):
    srv = serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(config, "SLM_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1")
    try:
        case = {"code": "T", "patient": {"name": "V", "age": 70, "sex": "M"}, "language": "en", "vitals": {"spo2": 86},
                "created_at": "2026-10-19T10:00:00"}
        a = orchestrator.run(case, [{"id": 1, "type": "text", "content": "slightly tired"}])
        assert a["priority"]["level"] == "P1"
    finally:
        srv.shutdown()
