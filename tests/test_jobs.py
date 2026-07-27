import time

from arbfinder.jobs import JobStore


def _wait(store, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get(job_id)
        if job and job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    return store.get(job_id)


def test_job_runs_persists_and_reports_stages(tmp_path):
    seen_stages = []

    def runner(job_id, params, progress):
        progress("discovering", "x")
        progress("calculating", "y")
        seen_stages.append(params["q"])
        return f"<b>done {params['q']}</b>", ""

    store = JobStore(tmp_path / "j.db")
    store.start_worker(runner)
    jid = store.enqueue({"q": "fan"})
    job = _wait(store, jid)
    assert job["status"] == "done"
    assert "done fan" in job["result_html"]
    assert seen_stages == ["fan"]


def test_job_error_is_captured(tmp_path):
    def runner(job_id, params, progress):
        raise RuntimeError("boom")

    store = JobStore(tmp_path / "j.db")
    store.start_worker(runner)
    jid = store.enqueue({})
    job = _wait(store, jid)
    assert job["status"] == "error"
    assert "boom" in (job["error"] or "")


def test_enqueue_without_worker_does_not_queue_in_memory(tmp_path):
    store = JobStore(tmp_path / "j.db")  # broker mode: no worker started
    store.enqueue({"n": "1"})
    assert store._q.qsize() == 0  # nothing handed to a (non-existent) worker


def test_claim_next_claims_oldest_and_prevents_double(tmp_path):
    store = JobStore(tmp_path / "j.db")
    a = store.enqueue({"n": "1"})
    time.sleep(0.01)
    b = store.enqueue({"n": "2"})
    first = store.claim_next()
    assert first["job_id"] == a and first["params"] == {"n": "1"}
    assert store.get(a)["status"] == "claimed"
    assert store.claim_next()["job_id"] == b   # next oldest
    assert store.claim_next() is None           # nothing left queued


def test_stale_claim_is_requeued(tmp_path, monkeypatch):
    import arbfinder.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "STALE_CLAIM_SECS", -1.0)  # treat any claim as stale
    store = jobs_mod.JobStore(tmp_path / "j.db")
    a = store.enqueue({"n": "1"})
    assert store.claim_next()["job_id"] == a
    assert store.claim_next()["job_id"] == a  # requeued (stale) then re-claimed


def test_latest_done_survives_and_is_most_recent(tmp_path):
    store = JobStore(tmp_path / "j.db")
    store.start_worker(lambda jid, p, pr: (f"<i>{p['n']}</i>", ""))
    first = store.enqueue({"n": "1"})
    _wait(store, first)
    second = store.enqueue({"n": "2"})
    _wait(store, second)
    latest = store.latest(status="done")
    assert latest["id"] == second
    # a fresh JobStore on the same file still sees the persisted result
    reopened = JobStore(tmp_path / "j.db")
    assert reopened.get(second)["result_html"] == "<i>2</i>"
