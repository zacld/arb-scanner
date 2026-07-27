import time

import pytest

pytest.importorskip("flask")
from arbfinder import dashboard


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "RESULTS_PATH", tmp_path / "results.csv")
    monkeypatch.setattr(dashboard, "_STORE", None)
    monkeypatch.setattr(dashboard, "HOSTED", False)
    monkeypatch.delenv("ARBFINDER_PASSWORD", raising=False)
    dashboard.app.secret_key = "test-key"


@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


# -- auth --------------------------------------------------------------------

def test_open_when_no_password_set(client):
    assert client.get("/").status_code == 200


def test_gate_redirects_to_login_when_password_set(client, monkeypatch):
    monkeypatch.setenv("ARBFINDER_PASSWORD", "hunter2")
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_grants_access(client, monkeypatch):
    monkeypatch.setenv("ARBFINDER_PASSWORD", "hunter2")
    assert client.post("/login", data={"password": "wrong"}).status_code == 200  # re-shows form
    assert client.post("/login", data={"password": "hunter2"}).status_code == 302
    assert client.get("/").status_code == 200  # cookie now authed


# -- job flow ----------------------------------------------------------------

def test_scan_enqueues_job_and_status_reports_done(client, monkeypatch):
    # Stub the actual scan so no network/browser is needed.
    monkeypatch.setattr(dashboard, "_run_scan",
                        lambda form, files, progress=None: "<b>RESULT ROWS</b>")
    r = client.post("/scan", data={"hunt": "1", "comparator": "ebay"})
    assert r.status_code == 302
    job_id = r.headers["Location"].rsplit("/", 1)[-1]

    # poll the status endpoint until done
    for _ in range(200):
        j = client.get(f"/jobs/{job_id}/status").get_json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert j["status"] == "done"

    # the job page and the home page both show the persisted result
    assert "RESULT ROWS" in client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert "RESULT ROWS" in client.get("/").get_data(as_text=True)


def test_hosted_mode_hides_local_only_controls(monkeypatch):
    monkeypatch.setattr(dashboard, "HOSTED", True)
    body = dashboard._render()
    assert ".local-only{display:none" in body  # secret / My-Chrome fields hidden


# -- Mac-agent broker flow ---------------------------------------------------

def test_agent_endpoints_require_token(client, monkeypatch):
    monkeypatch.setenv("ARBFINDER_AGENT_TOKEN", "sekret")
    assert client.get("/agent/next").status_code == 403                       # no token
    r = client.get("/agent/next", headers={"X-Agent-Token": "sekret"})
    assert r.status_code == 204                                                # authed, nothing queued


def test_broker_mode_agent_runs_job_and_persists(client, monkeypatch):
    # Hosted broker: the server does NOT run the scan; a queued job waits for the
    # agent, which claims it and posts the result back.
    monkeypatch.setattr(dashboard, "HOSTED", True)
    monkeypatch.setenv("ARBFINDER_AGENT_TOKEN", "sekret")

    job_id = client.post("/scan", data={"hunt": "1", "comparator": "ebay"}) \
        .headers["Location"].rsplit("/", 1)[-1]
    time.sleep(0.1)
    assert client.get(f"/jobs/{job_id}/status").get_json()["status"] == "queued"  # nobody ran it

    hdr = {"X-Agent-Token": "sekret"}
    claimed = client.get("/agent/next", headers=hdr).get_json()
    assert claimed["job_id"] == job_id
    client.post(f"/agent/progress/{job_id}", headers=hdr,
                json={"stage": "calculating", "detail": "x"})
    client.post(f"/agent/result/{job_id}", headers=hdr,
                json={"status": "done", "result_html": "<b>AGENT RESULT</b>"})

    assert client.get(f"/jobs/{job_id}/status").get_json()["status"] == "done"
    assert "AGENT RESULT" in client.get("/").get_data(as_text=True)
    assert "🟢 Mac agent connected" in client.get("/").get_data(as_text=True)
