"""Small local capacity drill: 10 real read runs plus 2 unpublished document jobs."""

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:18082/api/v1"


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


async def main():
    config = dict(
        line.split("=", 1)
        for line in (ROOT / "work/docker/acceptance.env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    password = config["WORKBENCH_DOCKER_DEMO_PASSWORD"]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="work/capacity-v1")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to((ROOT / "work").resolve()):
        raise ValueError("Experiment output must stay under project work")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:

        async def login(role):
            response = await client.post(
                BASE + "/auth/login",
                json={
                    "organization": "demo-a",
                    "email": role + "@demo.test",
                    "password": password,
                },
            )
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        support, admin = await asyncio.gather(login("support"), login("admin"))
        profile_response = await client.get(BASE + "/runtime/profile", headers=support)
        profile_response.raise_for_status()
        dispatch_mode = profile_response.json()["dispatch"]

        async def read_run(index):
            begin = time.perf_counter()
            response = await client.post(
                BASE + "/runs",
                headers={
                    **support,
                    "Idempotency-Key": "capacity-" + uuid4().hex,
                },
                json={"kind": "order_lookup", "order_number": "A1001"},
            )
            response.raise_for_status()
            run_id = response.json()["id"]
            first_event = None
            event_ids = []
            async with client.stream(
                "GET", BASE + "/runs/" + run_id + "/events", headers=support
            ) as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if first_event is None:
                            first_event = (time.perf_counter() - begin) * 1000
                        event_ids.append(event["id"])
            final = await client.get(BASE + "/runs/" + run_id, headers=support)
            final.raise_for_status()
            body = final.json()
            if body["status"] != "completed" or body["output"]["order"]["number"] != "A1001":
                raise RuntimeError("Read run did not complete with real expected order")
            if len(event_ids) != len(set(event_ids)):
                raise RuntimeError("Duplicate event IDs in one subscription")
            return {
                "index": index,
                "run_id": run_id,
                "status": body["status"],
                "first_event_ms": first_event,
                "total_ms": (time.perf_counter() - begin) * 1000,
                "tool_attempts": body["output"]["execution"]["tool_attempts"],
            }

        async def document_job(index):
            begin = time.perf_counter()
            label = "capacity-unpublished-" + uuid4().hex
            content = (
                "# " + label + "\n\nUnpublished queue and ingestion drill only. "
                "This document grants no customer rights or business authority.\n"
            ).encode()
            response = await client.post(
                BASE + "/knowledge/documents",
                headers=admin,
                data={"title": label, "policy_scope": "unknown", "chunk_strategy": "structural"},
                files={"file": (label + ".md", content, "text/markdown")},
            )
            response.raise_for_status()
            version = response.json()["version"]
            observed = [version["status"]]
            deadline = time.monotonic() + 120
            while version["status"] not in {"ready", "failed"} and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
                response = await client.get(
                    BASE + "/knowledge/versions/" + version["id"], headers=admin
                )
                response.raise_for_status()
                version = response.json()
                if not observed or observed[-1] != version["status"]:
                    observed.append(version["status"])
            if version["status"] != "ready" or version["published"]:
                raise RuntimeError("Document failed or unexpectedly became published")
            return {
                "index": index,
                "version_id": version["id"],
                "status": version["status"],
                "published": version["published"],
                "observed_states": observed,
                "total_ms": (time.perf_counter() - begin) * 1000,
            }

        results = await asyncio.gather(
            *(read_run(i) for i in range(10)), *(document_job(i) for i in range(2))
        )
    reads, documents = results[:10], results[10:]
    summary = {
        "scenario": "single small simultaneous local batch",
        "dispatch_mode": dispatch_mode,
        "read_runs": len(reads),
        "successful_read_runs": sum(r["status"] == "completed" for r in reads),
        "document_jobs": len(documents),
        "ready_unpublished_jobs": sum(
            r["status"] == "ready" and not r["published"] for r in documents
        ),
        "read_total_p50_ms": percentile([r["total_ms"] for r in reads], 0.5),
        "read_total_p95_ms": percentile([r["total_ms"] for r in reads], 0.95),
        "first_event_p50_ms": percentile([r["first_event_ms"] for r in reads], 0.5),
        "first_event_p95_ms": percentile([r["first_event_ms"] for r in reads], 0.95),
        "batch_seconds": time.perf_counter() - started,
        "paid_model_calls": 0,
        "business_write_tools": 0,
        "limits": [
            "One batch only; not sustained capacity or a service-level promise.",
            "Real MySQL/MCP and local embedding; no generative model workload.",
            "First event means persisted queue/progress event, not answer token.",
            "2 jobs enqueued concurrently; single worker may process sequentially.",
            "Full completion times include contention with two small ingestion jobs.",
            "No forced crash or cancellation in this batch.",
        ],
    }
    (output / "traces.json").write_text(json.dumps(results, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
