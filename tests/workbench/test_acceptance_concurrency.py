# ruff: noqa: F811
"""8A: 10 concurrent sessions + 2 document tasks with the test gateway."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

from helpers import MemoryIndex
from test_aftersales import flow  # noqa: F401

from workbench.ingestion import work_once
from workbench.job_models import RunJob
from workbench.run_worker import claim_job, process_job


def millis(seconds):
    return round(seconds * 1000, 1)


def percentile(values, ratio):
    ordered = sorted(values)
    if not ordered:
        return None
    position = int(round(ratio * (len(ordered) - 1)))
    position = min(position, len(ordered) - 1)
    return millis(ordered[position])


def test_ten_sessions_and_two_documents(flow, workbench):
    client, app, headers, calls = flow
    app.state.settings.run_dispatch_mode = "worker"
    database, settings = app.state.database, app.state.settings

    def submit(index):
        started = time.perf_counter()
        response = client.post(
            "/api/v1/runs",
            headers={**headers(), "Idempotency-Key": f"concurrency-{index}"},
            json={"order_number": "A1001"},
        )
        return response.status_code, time.perf_counter() - started, response

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(submit, range(10)))
    assert all(status in {200, 201, 202} for status, _, _ in results), [
        response.text for _, _, response in results
    ]
    runs = [response.json() for _, _, response in results]
    assert len({run["id"] for run in runs}) == 10

    queue_wait, execute = [], []
    for _ in range(10):
        claimed = time.perf_counter()
        claim = claim_job(database)
        queue_wait.append(time.perf_counter() - claimed)
        assert claim
        with database.sessions() as session:
            run_id = session.get(RunJob, claim[0]).run_id
        began = time.perf_counter()
        process_job(database, *claim, app.state.gateway)
        execute.append(time.perf_counter() - began)
        assert (
            client.get("/api/v1/runs/" + run_id, headers=headers()).json()["status"]
            == "completed"
        )
    for run in runs:
        assert client.get("/api/v1/runs/" + run["id"], headers=headers()).json()["status"] == "completed"

    assert (
        client.get(
            "/api/v1/runs/" + runs[0]["id"], headers=headers("support", "demo-b")
        ).status_code
        == 404
    )

    auth = {**headers(), "Idempotency-Key": "concurrency-duplicate"}
    first = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"}).json()
    second = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"}).json()
    assert first["id"] == second["id"]
    claim = claim_job(database)
    assert claim
    process_job(database, *claim, app.state.gateway)

    def document(index):
        started = time.perf_counter()
        response = client.post(
            "/api/v1/knowledge/documents",
            headers=headers("admin"),
            data={"title": f"并发政策 {index}"},
            files={
                "file": (
                    f"policy-{index}.md",
                    f"文档 {index}：退货期限七天，商品需未使用。".encode(),
                    "text/markdown",
                )
            },
        )
        return response.status_code, time.perf_counter() - started, response

    with ThreadPoolExecutor(max_workers=2) as pool:
        documents = list(pool.map(document, range(2)))
    assert all(status == 201 for status, _, _ in documents), [
        response.text for _, _, response in documents
    ]

    index = MemoryIndex()
    process_times = []
    for _ in range(2):
        began = time.perf_counter()
        assert work_once(database, settings, index)
        process_times.append(time.perf_counter() - began)

    version_ids = [response.json()["version"]["id"] for _, _, response in documents]
    for version_id in version_ids:
        detail = client.get(
            "/api/v1/knowledge/versions/" + version_id, headers=headers("admin")
        ).json()
        assert detail["status"] == "ready"
    assert (
        client.get(
            "/api/v1/knowledge/versions/" + version_ids[0],
            headers=headers("support", "demo-b"),
        ).status_code
        == 404
    )

    metrics = {
        "sessions": {
            "count": len(runs),
            "success_rate": 1.0,
            "submit_p50_ms": percentile([elapsed for _, elapsed, _ in results], 0.5),
            "submit_p95_ms": percentile([elapsed for _, elapsed, _ in results], 0.95),
            "queue_p95_ms": percentile(queue_wait, 0.95),
            "execute_p50_ms": percentile(execute, 0.5),
            "execute_p95_ms": percentile(execute, 0.95),
        },
        "documents": {
            "count": len(documents),
            "submit_p95_ms": percentile([elapsed for _, elapsed, _ in documents], 0.95),
            "process_ms": [millis(value) for value in process_times],
            "ready": sum(
                client.get(
                    "/api/v1/knowledge/versions/" + version_id, headers=headers("admin")
                ).json()["status"]
                == "ready"
                for version_id in version_ids
            ),
        },
        "isolation": {
            "run_cross_organization": 404,
            "document_cross_organization": 404,
            "duplicate_idempotency_single_run": True,
        },
        "gateway_calls": len(calls),
    }
    print("PR08A_METRICS " + json.dumps(metrics, ensure_ascii=False))