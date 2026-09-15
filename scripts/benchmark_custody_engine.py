#!/usr/bin/env python3
"""CPU/memory smoke benchmark for deterministic custody reasoning.

This measures state-machine overhead only. It does not benchmark a vision model
or imply detection accuracy.
"""

from __future__ import annotations

import json
import statistics
import time
import tracemalloc

from services.api.custody_engine import CustodyObservation, Fact, ProductCustodyEngine


def run(camera_count: int, visits: int = 1_000) -> dict:
    engine = ProductCustodyEngine(max_records=256)
    durations = []
    tracemalloc.start()
    for visit in range(visits):
        for offset, fact in enumerate(
            (
                Fact.PERSON_CONFIRMED,
                Fact.SHELF_DEPARTURE,
                Fact.HAND_CUSTODY,
                Fact.CONCEALMENT_TRANSITION,
                Fact.EXIT_CROSSED,
            )
        ):
            started = time.perf_counter_ns()
            engine.observe(
                CustodyObservation(
                    event_id=f"{visit}:{offset}",
                    visit_id=f"visit-{visit}",
                    episode_id="episode-1",
                    product_token=f"product-{visit}",
                    camera_id=f"camera-{visit % camera_count}",
                    at_ms=visit * 10_000 + offset * 1_000,
                    fact=fact,
                )
            )
            durations.append((time.perf_counter_ns() - started) / 1_000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    durations.sort()
    return {
        "camera_count": camera_count,
        "observations": len(durations),
        "median_microseconds": round(statistics.median(durations), 2),
        "p95_microseconds": round(durations[int(len(durations) * 0.95)], 2),
        "peak_mebibytes": round(peak / 1024 / 1024, 2),
        "scope": "deterministic custody state only; excludes vision inference",
    }


if __name__ == "__main__":
    print(json.dumps([run(4), run(6)], indent=2))
