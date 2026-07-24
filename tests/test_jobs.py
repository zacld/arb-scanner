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
