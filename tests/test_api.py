import json
import time

from fastapi.testclient import TestClient

from app import audit, db
from app.main import app


def login(c, user):
    r = c.post("/api/auth/login", json={"username": user, "password": "demo"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def wait_processed(c, code, h):
    for _ in range(100):
        r = c.get(f"/api/cases/{code}", headers=h)
        if r.json()["status"] != "processing":
            return r.json()
        time.sleep(0.1)
    raise AssertionError("not processed")


def test_full_flow_and_rbac():
    with TestClient(app) as c:
        # consent is mandatory
        r = c.post("/api/intake/patient", data={"patient": json.dumps({"name": "A"}), "consent": json.dumps({"triage": False}),
                                                "language": "en", "text": "cough"})
        assert r.status_code == 400

        r = c.post("/api/intake/patient", data={"patient": json.dumps({"name": "Ramesh", "age": "58", "sex": "M"}),
                                                "consent": json.dumps({"triage": True}), "language": "hi-en",
                                                "text": "1 ghante se seene mein dard hai, pasina aa raha hai"})
        assert r.status_code == 200
        code, access = r.json()["code"], r.json()["access_code"]

        dr, nurse, intake, admin = (login(c, u) for u in ("dr.mehta", "nurse.asha", "intake.ravi", "admin.dpo"))
        case = wait_processed(c, code, dr)
        assert case["priority"] == "P1"

        # data minimisation
        assert c.get(f"/api/cases/{code}", headers=intake).status_code == 403
        assert c.get(f"/api/cases/{code}", headers=admin).status_code == 403
        assert "summary" not in c.get("/api/queue", headers=intake).json()["cases"][0]
        assert c.get("/api/audit", headers=nurse).status_code == 403

        # only doctors lower priority / approve, overrides need a reason
        assert c.post(f"/api/cases/{code}/priority", headers=nurse, json={"level": "P3", "reason": "x"}).status_code == 403
        assert c.post(f"/api/cases/{code}/note", headers=nurse, json={"text": "x", "approve": True}).status_code == 403
        assert c.post(f"/api/cases/{code}/note", headers=dr, json={"text": "x", "approve": True, "priority_final": "P2"}).status_code == 400
        r = c.post(f"/api/cases/{code}/note", headers=dr, json={"text": "ok", "approve": True, "priority_final": "P2",
                                                                   "override_reason": "ECG normal, pain resolved"})
        assert r.status_code == 200
        assert c.get(f"/api/cases/{code}/fhir", headers=dr).json()["resourceType"] == "Bundle"

        # patient self-service: wrong access code hidden as 404, erase works and is audited
        assert c.get(f"/api/patient/{code}?access=000000").status_code in (404,) or access == "000000"
        assert c.post(f"/api/patient/{code}/erase?access={access}").status_code == 200
        assert db.get_case(code)["patient"] == {"erased": True}
        assert c.get(f"/api/cases/{code}", headers=dr).status_code == 404
        v = c.get("/api/audit", headers=admin).json()["verify"]
        assert v["ok"]


def test_audit_chain_detects_tampering():
    db.init()
    audit.log("t", "test", "x.one")
    audit.log("t", "test", "x.two")
    assert audit.verify()["ok"]
    with db.conn() as cn:
        cn.execute("UPDATE audit SET detail='{\"forged\":1}' WHERE action='x.one'")
    res = audit.verify()
    assert not res["ok"]
