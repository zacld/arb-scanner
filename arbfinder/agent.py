"""Mac-side agent for the hosted hybrid.

The hosted site (on Fly) can't scrape Argos/Amazon — a data-centre IP is blocked.
This agent runs on your Mac (residential IP + real Chrome), polls the hosted site
for queued scans, runs each one **locally** with the exact same code the local
dashboard uses, and posts the results back so they appear in the cloud on any
device. Your Mac just needs to be awake with this running.

    python -m arbfinder.agent --url https://<app>.fly.dev --token <AGENT_TOKEN>

(URL/token also read from ARBFINDER_AGENT_URL / ARBFINDER_AGENT_TOKEN.)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import requests

log = logging.getLogger(__name__)


def _headers(token: str) -> dict:
    return {"X-Agent-Token": token}


def run_once(base_url: str, token: str, timeout: float = 30.0) -> bool:
    """Claim and run one job if available. Returns True if a job was handled."""
    from .dashboard import run_job_local  # local scan core (real Chrome + eBay)

    base = base_url.rstrip("/")
    r = requests.get(f"{base}/agent/next", headers=_headers(token), timeout=timeout)
    if r.status_code == 403:
        raise SystemExit(
            "Agent token rejected by the server. Make sure ARBFINDER_AGENT_TOKEN "
            "here matches the ARBFINDER_AGENT_TOKEN Fly secret on the site."
        )
    if r.status_code == 204:
        return False
    r.raise_for_status()
    job = r.json()
    job_id, params = job["job_id"], job["params"]
    label = params.get("url") or ("hunt" if params.get("hunt") else "scan")
    print(f"▶ claimed job {job_id} ({label})")

    def progress(stage: str, detail: str = "") -> None:
        try:
            requests.post(f"{base}/agent/progress/{job_id}", headers=_headers(token),
                          json={"stage": stage, "detail": detail}, timeout=timeout)
        except requests.RequestException:
            pass  # progress is best-effort; the result post is what matters

    try:
        result_html, _csv = run_job_local(params, progress)
        requests.post(f"{base}/agent/result/{job_id}", headers=_headers(token),
                      json={"status": "done", "result_html": result_html}, timeout=timeout)
        print(f"✔ job {job_id} done")
    except Exception as exc:  # noqa: BLE001 - report failure back to the site
        log.exception("job %s failed", job_id)
        requests.post(f"{base}/agent/result/{job_id}", headers=_headers(token),
                      json={"status": "error", "error": str(exc)}, timeout=timeout)
        print(f"✖ job {job_id} failed: {exc}")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="arbfinder.agent",
        description="Run hosted-site scans locally on your Mac (residential IP + Chrome).")
    p.add_argument("--url", default=os.environ.get("ARBFINDER_AGENT_URL"),
                   help="Hosted site URL, e.g. https://your-app.fly.dev "
                        "(or ARBFINDER_AGENT_URL)")
    p.add_argument("--token", default=os.environ.get("ARBFINDER_AGENT_TOKEN"),
                   help="Shared agent token matching the site's ARBFINDER_AGENT_TOKEN "
                        "(or set the env var)")
    p.add_argument("--interval", type=float, default=3.0,
                   help="Seconds between polls when idle (default: 3)")
    args = p.parse_args(argv)

    if not args.url or not args.token:
        print("Need --url and --token (or ARBFINDER_AGENT_URL / ARBFINDER_AGENT_TOKEN).",
              file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(f"arbfinder agent → polling {args.url} every {args.interval:g}s. Ctrl+C to stop.")
    backoff = args.interval
    while True:
        try:
            handled = run_once(args.url, args.token)
            backoff = args.interval
            if not handled:
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0
        except SystemExit:
            raise
        except requests.RequestException as exc:
            print(f"… can't reach {args.url} ({exc}); retrying in {backoff:.0f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


if __name__ == "__main__":
    sys.exit(main())
