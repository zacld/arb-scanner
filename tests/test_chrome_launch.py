from arbfinder import chrome_launch


def test_is_running_false_when_port_dead():
    # An unlikely-to-be-open high port: no DevTools endpoint answers.
    assert chrome_launch.is_running("http://127.0.0.1:59999", timeout=0.3) is False


def test_ensure_chrome_reuses_existing(monkeypatch):
    monkeypatch.setattr(chrome_launch, "is_running", lambda *a, **k: True)
    launched = {"n": 0}
    monkeypatch.setattr(chrome_launch.subprocess, "Popen",
                        lambda *a, **k: launched.__setitem__("n", launched["n"] + 1))
    ok, msg = chrome_launch.ensure_chrome("http://127.0.0.1:9222")
    assert ok is True
    assert "Reusing" in msg
    assert launched["n"] == 0  # never spawned a new process


def test_ensure_chrome_reports_missing_binary(monkeypatch):
    monkeypatch.setattr(chrome_launch, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(chrome_launch, "_chrome_binary", lambda: None)
    ok, msg = chrome_launch.ensure_chrome("http://127.0.0.1:9222")
    assert ok is False
    assert "Couldn't find Google Chrome" in msg


def test_ensure_chrome_launches_and_waits(monkeypatch, tmp_path):
    # Down at first, up after the launch attempt.
    states = iter([False, False, True])
    monkeypatch.setattr(chrome_launch, "is_running", lambda *a, **k: next(states, True))
    monkeypatch.setattr(chrome_launch, "_chrome_binary", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(chrome_launch, "DEBUG_PROFILE", tmp_path / "profile")
    seen = {}
    def fake_popen(args, **kwargs):
        seen["args"] = args
        return object()
    monkeypatch.setattr(chrome_launch.subprocess, "Popen", fake_popen)
    ok, msg = chrome_launch.ensure_chrome("http://127.0.0.1:9222",
                                          open_url="https://www.argos.co.uk/search/x/")
    assert ok is True
    assert "Launched" in msg
    # It passed the debug port, the dedicated profile and the warm-up URL.
    assert "--remote-debugging-port=9222" in seen["args"]
    assert any("profile" in a for a in seen["args"])
    assert "https://www.argos.co.uk/search/x/" in seen["args"]
