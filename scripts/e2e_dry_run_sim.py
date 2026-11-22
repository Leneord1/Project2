#!/usr/bin/env python3
"""Simulate n8n Approval Gate dry-run routing against the live microservice."""

from __future__ import annotations

import csv
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "microservice" / "sample_request.json"
OUT_DIR = ROOT / "docs" / "screenshots"
LOG_CSV = ROOT / "docs" / "screenshots" / "linkedin_run_log.csv"
BASE = "http://127.0.0.1:8000"


def http_json(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Call microservice and return status plus JSON body."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return resp.status, payload
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        return exc.code, payload


def route_decision(confidence: float, min_confidence: float, dry_run: bool) -> str:
    """Mirror n8n Approval Gate: live LinkedIn only when confident and not dry-run."""
    if (not dry_run) and confidence >= min_confidence:
        return "linkedin_live"
    return "slack_review"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    health_code, health = http_json("GET", "/health")
    (OUT_DIR / "health.json").write_text(json.dumps(health, indent=2), encoding="utf-8")
    print("health", health_code, health)

    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    code, payload = http_json("POST", "/linkedin", sample)
    (OUT_DIR / "linkedin_response.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("linkedin", code)
    if code != 200:
        print(payload)
        return 1

    dry_run = True
    min_confidence = float(sample["options"]["min_confidence"])
    confidence = float(payload["confidence"])
    final_text = payload["draft"] + "\n\n" + " ".join(payload["hashtags"])
    route = route_decision(confidence, min_confidence, dry_run)

    # Low-confidence scenario forces Slack even if dryRun were false.
    low_route = route_decision(confidence, 0.99, False)
    live_route = route_decision(confidence, min_confidence, False)

    gate = {
        "final_text": final_text,
        "confidence": confidence,
        "min_confidence": min_confidence,
        "dry_run_route": route,
        "low_confidence_route": low_route,
        "live_ready_route": live_route,
        "slack_message_preview": (
            f"LinkedIn draft for review | confidence={confidence} | dryRun={dry_run}\n\n{final_text}"
        ),
    }
    (OUT_DIR / "approval_gate_dry_run.json").write_text(json.dumps(gate, indent=2), encoding="utf-8")

    rows = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": route,
            "status": "ok",
            "confidence": confidence,
            "min_confidence": min_confidence,
            "dry_run": dry_run,
            "model": payload["meta"]["model"],
            "draft": final_text.replace("\n", " "),
        },
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": low_route,
            "status": "ok",
            "confidence": confidence,
            "min_confidence": 0.99,
            "dry_run": False,
            "model": payload["meta"]["model"],
            "draft": final_text.replace("\n", " "),
        },
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "route": "error",
            "status": "simulated_upstream_down",
            "confidence": "",
            "min_confidence": min_confidence,
            "dry_run": dry_run,
            "model": "",
            "draft": "",
        },
    ]
    with LOG_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "route",
                "status",
                "confidence",
                "min_confidence",
                "dry_run",
                "model",
                "draft",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    assert route == "slack_review", route
    assert low_route == "slack_review", low_route
    assert live_route == "linkedin_live", live_route
    print("e2e_dry_run_sim_passed")
    print("artifacts", OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
