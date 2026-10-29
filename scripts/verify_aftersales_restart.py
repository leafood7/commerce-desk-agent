"""Local development acceptance: preserve a real paused run across API process restart."""

import argparse
import json
import os
import time
from pathlib import Path

import httpx

from workbench.config import WorkbenchSettings

RECORD = Path("work/fullstack/aftersales-restart.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "verify"])
    args = parser.parse_args()
    if WorkbenchSettings().environment != "development":
        raise RuntimeError("This acceptance script is for local development only")
    password = (
        os.environ.get("WORKBENCH_TEST_PASSWORD")
        or json.loads(Path("work/fullstack/local-login.json").read_text(encoding="utf-8"))[
            "password"
        ]
    )
    with httpx.Client(base_url="http://127.0.0.1:8082", timeout=40, trust_env=False) as client:

        def login(role):
            response = client.post(
                "/api/v1/auth/login",
                json={"organization": "demo-a", "email": role + "@demo.test", "password": password},
            )
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        support = login("support")

        def wait(identifier):
            for _ in range(120):
                response = client.get("/api/v1/runs/" + identifier, headers=support)
                response.raise_for_status()
                run = response.json()
                if run["status"] not in {"queued", "running"}:
                    return run
                time.sleep(0.25)
            raise RuntimeError("Run did not reach a bounded public state")

        if args.stage == "prepare":
            order = json.loads(Path("work/fullstack/aftersales-demo.json").read_text())[
                "correction"
            ]
            response = client.post(
                "/api/v1/runs",
                headers=support,
                json={
                    "kind": "aftersales",
                    "query": "请帮我加急",
                    "order_number": order,
                    "shipping_kind": "expedite",
                    "reason": "服务重启恢复验收",
                },
            )
            response.raise_for_status()
            run = wait(response.json()["id"])
            assert run["status"] == "awaiting_confirmation", run["status"]
            RECORD.write_text(
                json.dumps(
                    {
                        "id": run["id"],
                        "pause_id": run["output"]["pause_id"],
                        "order_number": order,
                        "prepared": True,
                    },
                    indent=2,
                )
                + "\n"
            )
            print("Real run paused before submission; no credential stored in restart record.")
        else:
            record = json.loads(RECORD.read_text())
            run = wait(record["id"])
            assert run["status"] == "awaiting_confirmation"
            assert run["output"]["pause_id"] == record["pause_id"]
            response = client.post(
                "/api/v1/runs/" + run["id"] + "/resume",
                headers={**support, "Idempotency-Key": "restart-confirm-" + run["id"]},
                json={"action": "confirm", "pause_id": run["output"]["pause_id"]},
            )
            response.raise_for_status()
            run = wait(run["id"])
            assert run["status"] == "awaiting_approval", run["status"]
            request_id = run["output"]["request"]["id"]
            response = client.post(
                "/api/v1/shipping/requests/" + request_id + "/decision",
                headers=login("approver"),
                json={"action": "reject", "reason": "重启验收结束，不执行加急"},
            )
            response.raise_for_status()
            response = client.post(
                "/api/v1/runs/" + run["id"] + "/resume",
                headers={**support, "Idempotency-Key": "restart-refresh-" + run["id"]},
                json={"action": "refresh", "pause_id": run["output"]["pause_id"]},
            )
            response.raise_for_status()
            run = wait(run["id"])
            assert run["status"] == "completed"
            assert run["output"]["request"]["status"] == "rejected"
            assert run["output"]["request"]["ticket_id"] is None
            record.update(
                verified=True,
                final_run_status=run["status"],
                actual_request_status="rejected",
                credentials_persisted=False,
            )
            RECORD.write_text(json.dumps(record, indent=2) + "\n")
            print(
                "Process restart verified: same pause, fresh login, MCP submission, independent rejection, actual result."
            )


if __name__ == "__main__":
    main()
