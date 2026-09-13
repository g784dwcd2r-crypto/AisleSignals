#!/usr/bin/env python3
"""Run the real configured model through job, evidence, review and deletion APIs.

Uses generated geometry in an isolated temporary database. This is a runtime
negative-control test, never evidence of pharmacy action recognition accuracy.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from services.api.app import create_app

BASE = "http://127.0.0.1:8765"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["synthetic", "pilot"], default="synthetic")
    args = parser.parse_args()
    frames = []
    for index in range(4):
        frame = Image.new("RGB", (640, 360), "#172b28")
        ImageDraw.Draw(frame).rectangle((40 + index * 35, 130, 100 + index * 35, 190), fill="#50a080")
        stream = io.BytesIO()
        frame.save(stream, "JPEG")
        frames.append({"at_seconds": index * 1.25, "jpeg_base64": base64.b64encode(stream.getvalue()).decode()})
    with tempfile.TemporaryDirectory(prefix="aisle-vision-smoke-") as temp:
        directory = Path(temp).resolve()
        app = create_app(directory / "smoke.db", directory / "web", mode=args.mode)
        email, password = "manager@harbour.demo", "AisleDemo!2026"
        if args.mode == "pilot":
            from services.api.pilot_identity import initialise
            email, password = "smoke@example.invalid", secrets.token_urlsafe(32)
            initialise(app.state.store, "Synthetic smoke group", "Synthetic smoke branch",
                       email, "Synthetic smoke operator", password)
        with TestClient(app, base_url=BASE, headers={"Origin": BASE}) as client:
            response = client.post("/api/login", json={"email": email, "password": password})
            assert response.status_code == 200, "Smoke login failed"
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            client.headers["X-AisleSignals-Site"] = response.json()["current_site_id"]
            status = client.get("/api/interactions/status").json()
            assert status["ready"], status["message"]
            started = time.monotonic()
            response = client.post("/api/interactions/jobs", headers={"Idempotency-Key": str(uuid4())}, json={
                "run_id": str(uuid4()), "source_kind": "RECORDED_VIDEO",
                "source_label": "Generated geometry runtime smoke; no people", "frames": frames,
            })
            assert response.status_code == 202, "Job was not accepted"
            item_id = response.json()["id"]
            deadline = time.monotonic() + 155
            while time.monotonic() < deadline:
                result = client.get(f"/api/interactions/jobs/{item_id}").json()
                if result["status"] not in {"pending", "running"}:
                    break
                time.sleep(.25)
            assert result["status"] == "completed", result.get("error", "Model job did not finish")
            item = result["result"]
            assert item["action"] == "UNCLEAR", "Geometry was misclassified; inspect the model before proceeding"
            assert item["person_visible"] is False and item["alarm_eligible"] is False
            assert len(item["frames"]) == 4
            if args.mode == "pilot":
                from services.api.evidence_crypto import MAGIC
                assert (app.state.interactions.directory(item_id) / "0.jpg").read_bytes().startswith(MAGIC)
            for frame in item["frames"]:
                image = client.get(frame["url"])
                assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
            review = client.post(f"/api/interactions/{item_id}/review", json={"outcome": "UNCLEAR", "note": "Generated geometry control", "expected_version": item["version"]})
            assert review.status_code == 200 and review.json()["review"]["outcome"] == "UNCLEAR"
            assert client.delete(f"/api/interactions/{item_id}").status_code == 200
            assert client.get(item["frames"][0]["url"]).status_code == 404
            report = {
                "test": "real-model-api-negative-control",
                "workspace_mode": args.mode,
                "encrypted_evidence_verified": args.mode == "pilot",
                "input": "Four generated frames of a moving rectangle; no people, no products",
                "model_executed": True, "pharmacy_accuracy_evaluated": False,
                "wall_seconds": round(time.monotonic() - started, 3),
                "checks": ["authenticated job", "real local inference", "no-person abstention", "no alarm eligibility", "four authenticated JPEG derivatives", "review", "deletion revokes evidence"],
                "result": {key: item[key] for key in ["action", "visibility", "person_visible", "product_visible", "sequence_observed", "alarm_eligible", "model", "model_digest", "backend", "prompt_version", "prompt_sha256", "inference_ms", "validated"]},
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if os.name != "nt":
        args.output.chmod(0o600)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
