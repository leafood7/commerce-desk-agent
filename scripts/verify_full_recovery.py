"""Local same-version recovery drill. New DB/account/files/index; no source cutover.

Private SQL, auth data and service configuration stay in ignored work/. Only the
sanitized result is eligible for publication. Never drops databases or volumes.
"""

import argparse
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from verify_acceptance_backup import ROOT, SOURCE, AcceptanceDatabase, identifier


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def request(http, method, path, *, expected=None, **kwargs):
    response = http.request(method, path, **kwargs)
    if expected is not None:
        if response.status_code != expected:
            raise RuntimeError(
                f"Recovery HTTP check failed: {method} {path} status={response.status_code}"
            )
    elif response.is_error:
        # Never print auth responses, request headers or configuration secrets.
        raise RuntimeError(
            f"Recovery HTTP operation failed: {method} {path} status={response.status_code}"
        )
    return response.json()


def login(http, password, role="support", organization="demo-a"):
    data = request(
        http,
        "POST",
        "/api/v1/auth/login",
        json={
            "organization": organization,
            "email": role + "@demo.test",
            "password": password,
        },
    )
    return {"Authorization": "Bearer " + data["access_token"]}


def wait_run(http, headers, run_id):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        row = request(http, "GET", "/api/v1/runs/" + run_id, headers=headers)
        if row["status"] not in {"running", "queued"}:
            return row
        time.sleep(0.5)
    raise RuntimeError("Recovery workflow did not reach a stable pause")


def ready(http):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            if http.get("/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Recovery API is not ready")


class Drill:
    def __init__(self, directory):
        self.directory = directory.resolve()
        base = (ROOT / "work/recovery").resolve()
        if not self.directory.is_relative_to(base) or self.directory == base:
            raise ValueError("Drill directory must be a child of project work/recovery")
        self.client = AcceptanceDatabase()
        self.state_path = self.directory / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def checkpoint(self, **values):
        self.state.update(values)
        save(self.state_path, self.state)

    def docker(self, arguments, *, timeout=240):
        result = subprocess.run(
            [*self.client.compose[:3], *arguments],
            capture_output=True,
            env=self.client.env,
            timeout=timeout,
            cwd=ROOT,
        )
        if result.returncode:
            (self.directory / "docker-last-error.log").write_bytes(result.stderr)
            raise RuntimeError("Recovery Docker operation failed; private diagnostic retained")
        return result.stdout

    def compose(self, *arguments, timeout=240):
        if not re.fullmatch(r"commercedesk-recovery-[a-f0-9]{10}", self.state["project"]):
            raise ValueError("Refusing to operate a non-recovery Compose project")
        return self.docker(
            [
                "compose",
                "--project-name",
                self.state["project"],
                "--env-file",
                str(self.directory / "runtime.env"),
                "-f",
                str(self.directory / "compose.json"),
                *arguments,
            ],
            timeout=timeout,
        )

    def prepare(self, password):
        if "run_id" in self.state:
            return
        print("Preparing a fresh labelled demo and paused workflow", flush=True)
        fixture = json.loads(
            self.client.run(["exec", "-T", "api", "python", "scripts/seed_return_demo.py"])
        )
        save(self.directory / "demo.json", fixture)
        with httpx.Client(base_url="http://127.0.0.1:18082", timeout=100, trust_env=False) as http:
            admin, support = login(http, password, "admin"), login(http, password)
            original = request(http, "GET", "/api/v1/runtime/config", headers=admin)
            draft = request(
                http,
                "POST",
                "/api/v1/runtime/config/versions",
                headers=admin,
                json={
                    "return_flow_version": "guided_v3",
                    "return_retrieval_mode": "clause_bound",
                    "return_decisions": "rules",
                    "model_alias": "rules",
                },
            )
            request(
                http,
                "POST",
                f"/api/v1/runtime/config/versions/{draft['id']}/publish",
                headers=admin,
                json={"expected_revision": original["revision"]},
            )
            try:
                order = fixture["orders"]["normal"]
                created = request(
                    http,
                    "POST",
                    "/api/v1/runs",
                    headers={**support, "Idempotency-Key": "recovery-" + self.directory.name},
                    json={
                        "kind": "aftersales",
                        "query": "申请退货，请核对适用商品、实际签收后的时间窗口和未使用条件。",
                        "order_number": order["number"],
                        "return_item_id": order["item_id"],
                        "return_quantity": 1,
                        "return_condition": "unknown",
                        "return_policy_id": fixture["policy_id"],
                        "reason": "完整隔离恢复演练",
                    },
                )
                run = wait_run(http, support, created["id"])
                if run["status"] != "awaiting_input":
                    raise RuntimeError("Source demo did not pause for missing condition")
                self.checkpoint(
                    run_id=run["id"],
                    original_pause_id=run["output"]["pause_id"],
                    initial_output=run["output"],
                    fixture=fixture,
                )
            finally:
                current = request(http, "GET", "/api/v1/runtime/config", headers=admin)
                if current["current"]["id"] != draft["id"]:
                    raise RuntimeError(
                        "Source configuration changed concurrently; do not overwrite it"
                    )
                previous = original["current"]["id"]
                if not previous:
                    values = {
                        k: v
                        for k, v in original["current"]["values"].items()
                        if k not in {"prompt_sha256", "return_prompt_sha256"}
                    }
                    previous = request(
                        http, "POST", "/api/v1/runtime/config/versions", headers=admin, json=values
                    )["id"]
                request(
                    http,
                    "POST",
                    f"/api/v1/runtime/config/versions/{previous}/publish",
                    headers=admin,
                    json={"expected_revision": current["revision"]},
                )

    def backup(self):
        if "backup" in self.state:
            return
        busy = self.client.sql(
            "SELECT COUNT(*) FROM wb_runs WHERE status IN ('queued','running');", SOURCE
        )
        ingestion = self.client.sql(
            "SELECT COUNT(*) FROM wb_ingestion_jobs WHERE status IN ('queued','processing');",
            SOURCE,
        )
        if int(busy) or int(ingestion):
            raise RuntimeError("Source is busy; defer the snapshot rather than stop other tasks")
        print("Backing up SQL/files and verifying a newly created database", flush=True)
        start = time.monotonic()
        with (self.directory / "backup.log").open("wb") as log:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify_acceptance_backup.py"),
                    "--verify-new-database",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=self.client.env,
                cwd=ROOT,
                timeout=1200,
            )
        if result.returncode:
            raise RuntimeError("Backup/restore check failed; private log retained")
        result = json.loads((self.directory / "backup.log").read_text())
        if not result["restore_verified"] or result["restored_table_count"] != 34:
            raise RuntimeError("Restored schema/table verification failed")
        self.checkpoint(backup=result, backup_restore_seconds=time.monotonic() - start)

    def provision(self):
        if "project" in self.state:
            return
        print(
            "Creating isolated services, restricted database identity and fresh index directory",
            flush=True,
        )
        suffix = uuid4().hex[:10]
        database = self.state["backup"]["verification_database"]
        if not re.fullmatch(r"commercedesk_verify_[a-f0-9]{12}", database):
            raise ValueError("Refusing to provision a non-verification database")
        identifier(database)
        username, password = "recovery_" + suffix, secrets.token_hex(24)
        self.client.sql(
            f"CREATE USER '{username}'@'%' IDENTIFIED BY '{password}';"
            f"GRANT ALL PRIVILEGES ON {identifier(database)}.* TO '{username}'@'%';"
        )
        backup = Path(self.state["backup"]["backup_path"]).resolve()
        if not backup.is_relative_to((ROOT / "work/backups").resolve()):
            raise ValueError("Unexpected backup path")
        shutil.copytree(backup / "documents", self.directory / "files")
        (self.directory / "index").mkdir(exist_ok=False)
        (self.directory / "runtime.env").write_text(
            f"RECOVERY_DATABASE_URL=mysql+pymysql://{username}:{password}@mysql:3306/{database}\n",
            encoding="utf-8",
        )
        containers = [
            json.loads(line) for line in self.client.run(["ps", "--format", "json"]).splitlines()
        ]
        rows = {r["Service"]: r for r in containers}
        images = {
            s: self.docker(["inspect", "--format", "{{.Image}}", rows[s]["ID"]]).decode().strip()
            for s in ("api", "mcp", "run-worker", "chroma")
        }
        networks = self.docker(
            ["inspect", "--format", "{{json .NetworkSettings.Networks}}", rows["mysql"]["ID"]]
        )
        mysql_networks = json.loads(networks)
        if len(mysql_networks) != 1:
            raise RuntimeError("Ambiguous source MySQL network")
        common = {
            "WORKBENCH_DATABASE_URL": "${RECOVERY_DATABASE_URL}",
            "WORKBENCH_ENVIRONMENT": "development",
            "WORKBENCH_RUN_DISPATCH_MODE": "worker",
            "WORKBENCH_MCP_URL": "http://restore-mcp:8083/mcp",
            "WORKBENCH_CHROMA_URL": "http://restore-chroma:8000",
            "WORKBENCH_STORAGE_ROOT": "/app/data/workbench/files",
            "WORKBENCH_RETRIEVAL_CONTEXT_MODE": "neighbors",
            "WORKBENCH_AGENT_ROUTER_MODE": "rules",
            "WORKBENCH_RETURN_DECISION_MODE": "rules",
            "WORKBENCH_AGENT_ROUTER_KEY": "",
            "WORKBENCH_AGENT_ROUTER_URL": "",
            "WORKBENCH_MCP_TIMEOUT_SECONDS": "90",
            "HF_HUB_OFFLINE": "1",
            "NO_PROXY": "localhost,127.0.0.1,restore-api,restore-mcp,restore-chroma,mysql",
            "ANONYMIZED_TELEMETRY": "false",
        }

        def bind(source, target, readonly=False):
            return {
                "type": "bind",
                "source": str(source).replace("\\", "/"),
                "target": target,
                "read_only": readonly,
            }

        files = bind(self.directory / "files", "/app/data/workbench/files")
        models = bind(ROOT / "work/huggingface/hub", "/app/work/huggingface/hub", True)
        script = bind(
            ROOT / "scripts/rebuild_recovery_index.py", "/app/rebuild_recovery_index.py", True
        )
        evidence = bind(self.directory, "/app/recovery-proof")
        config = {
            "services": {
                "restore-api": {
                    "image": images["api"],
                    "pull_policy": "never",
                    "environment": {**common, "PORT": "8080", "COMMERCE_APP": "workbench"},
                    "ports": ["127.0.0.1:18092:8080"],
                    "volumes": [files],
                    "networks": ["default", "source-db"],
                    "mem_limit": "512m",
                },
                "restore-mcp": {
                    "image": images["mcp"],
                    "pull_policy": "never",
                    "command": ["python", "-m", "workbench.mcp_server"],
                    "environment": {
                        **common,
                        "WORKBENCH_MCP_BIND_HOST": "0.0.0.0",
                        "WORKBENCH_MCP_ALLOWED_HOSTS": '["restore-mcp:8083","localhost:*","127.0.0.1:*"]',
                        "WORKBENCH_MCP_WARM_MODELS": "false",
                    },
                    "volumes": [models, files, script, evidence],
                    "networks": ["default", "source-db"],
                    "mem_limit": "3g",
                    "cpus": 2,
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "python",
                            "-c",
                            "import urllib.request; urllib.request.urlopen('http://localhost:8083/health', timeout=3)",
                        ],
                        "interval": "5s",
                        "timeout": "5s",
                        "retries": 30,
                    },
                },
                "restore-run-worker": {
                    "image": images["run-worker"],
                    "pull_policy": "never",
                    "command": ["python", "-m", "workbench.run_worker"],
                    "environment": common,
                    "networks": ["default", "source-db"],
                    "mem_limit": "256m",
                    "healthcheck": {"disable": True},
                },
                "restore-chroma": {
                    "image": images["chroma"],
                    "pull_policy": "never",
                    "command": [
                        "python",
                        "-m",
                        "run_chroma",
                        "run",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                        "--path",
                        "/app/data/chroma",
                    ],
                    "environment": {"ANONYMIZED_TELEMETRY": "false"},
                    "volumes": [bind(self.directory / "index", "/app/data/chroma")],
                    "mem_limit": "1g",
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "python",
                            "-c",
                            "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v2/heartbeat', timeout=3)",
                        ],
                        "interval": "5s",
                        "timeout": "5s",
                        "retries": 30,
                    },
                },
            },
            "networks": {"source-db": {"external": True, "name": next(iter(mysql_networks))}},
        }
        save(self.directory / "compose.json", config)
        self.checkpoint(
            project="commercedesk-recovery-" + suffix,
            images=images,
            rebuild_script_sha256=hashlib.sha256(
                (ROOT / "scripts/rebuild_recovery_index.py").read_bytes()
            ).hexdigest(),
        )

    def launch(self):
        print("Starting recovery API/MCP/worker with original application images", flush=True)
        self.compose("up", "-d", "--wait", "--wait-timeout", "180")

    def reindex(self):
        result = self.directory / "index-proof.json"
        if not result.exists():
            print(
                "Rebuilding a verified empty vector index from restored published SQL chunks",
                flush=True,
            )
            start = time.monotonic()
            self.compose(
                "exec",
                "-T",
                "restore-mcp",
                "python",
                "/app/rebuild_recovery_index.py",
                "--expected-database",
                self.state["backup"]["verification_database"],
                "--output",
                "/app/recovery-proof/index-proof.json",
                timeout=900,
            )
            self.checkpoint(index_rebuild_seconds=time.monotonic() - start)
        if (
            json.loads(result.read_text())["database"]
            != self.state["backup"]["verification_database"]
        ):
            raise RuntimeError("Recovery index proof targets another database")

    def verify(self, password):
        print(
            "Verifying original pause, actual MCP sources, approval and simulated receipt",
            flush=True,
        )
        state, fixture = self.state, self.state["fixture"]
        run_id = state["run_id"]
        with httpx.Client(base_url="http://127.0.0.1:18092", timeout=120, trust_env=False) as http:
            ready(http)
            support, approver, admin = (
                login(http, password, r) for r in ("support", "approver", "admin")
            )
            other = login(http, password, "support", "demo-b")
            run = request(http, "GET", "/api/v1/runs/" + run_id, headers=support)
            if not state.get("continuation_started"):
                assert run["status"] == "awaiting_input"
                assert run["output"]["pause_id"] == state["original_pause_id"]
                assert run["output"] == state["initial_output"]
            query = {
                "query": "申请退货，请核对适用商品、实际签收后的时间窗口和未使用条件。",
                "policy_scope": "returns",
                "strategy": "hybrid_rerank",
                "version_id": fixture["version_id"],
                "top_k": 3,
            }
            found = request(http, "POST", "/api/v1/knowledge/search", headers=support, json=query)
            assert found["matches"] and all(
                m["version_id"] == fixture["version_id"] for m in found["matches"]
            )
            foreign = request(http, "POST", "/api/v1/knowledge/search", headers=other, json=query)
            assert foreign["matches"] == []
            # File is binary; verify bytes without JSON decoding.
            original = http.get(
                "/api/v1/knowledge/versions/" + fixture["version_id"] + "/file", headers=admin
            )
            assert original.status_code == 200
            db_hash = self.client.sql(
                "SELECT content_hash FROM wb_document_versions WHERE id='"
                + fixture["version_id"]
                + "';",
                state["backup"]["verification_database"],
            ).strip()
            assert hashlib.sha256(original.content).hexdigest() == db_hash
            self.checkpoint(continuation_started=True)

            def resume(action, **fields):
                nonlocal run
                key = "recovery-" + action + "-" + str(run["output"].get("pause_id"))
                body = {"action": action, "pause_id": run["output"].get("pause_id"), **fields}
                headers = {**support, "Idempotency-Key": key}
                request(http, "POST", f"/api/v1/runs/{run_id}/resume", headers=headers, json=body)
                request(http, "POST", f"/api/v1/runs/{run_id}/resume", headers=headers, json=body)
                run = wait_run(http, support, run_id)

            if run["status"] == "awaiting_input":
                resume("supply", return_condition="unused")
            if run["status"] == "awaiting_confirmation" and not run["output"].get("request"):
                resume("confirm")
            application = run["output"]["request"]
            app_id = application["id"]
            decision = {
                "expected_revision": application["revision"],
                "action": "approve",
                "reason": "恢复演练独立审批",
            }
            if run["status"] == "awaiting_approval":
                request(
                    http,
                    "POST",
                    f"/api/v1/returns/requests/{app_id}/decision",
                    headers=support,
                    json=decision,
                    expected=403,
                )
                request(
                    http, "GET", f"/api/v1/returns/requests/{app_id}", headers=other, expected=404
                )
                request(
                    http,
                    "POST",
                    f"/api/v1/returns/requests/{app_id}/decision",
                    headers=approver,
                    json=decision,
                )
                resume("refresh")
            if run["status"] == "awaiting_confirmation":
                resume("confirm")
            assert run["status"] == "completed"
            final = run["output"]["request"]
            assert final["id"] == app_id and final["status"] == "accepted_simulated"
            assert final["actual_refund_executed"] is False and final["refund_amount_cents"] is None
            # SQL checks avoid request-list pagination hiding accidental duplicates.
            db = state["backup"]["verification_database"]
            count = int(
                self.client.sql(
                    "SELECT COUNT(*) FROM wb_return_requests WHERE item_id='"
                    + fixture["orders"]["normal"]["item_id"]
                    + "';",
                    db,
                )
            )
            receipt_count = int(
                self.client.sql(
                    "SELECT COUNT(*) FROM wb_return_events WHERE request_id='"
                    + app_id
                    + "' AND action='accepted_simulated';",
                    db,
                )
            )
            assert count == 1 and receipt_count == 1
            self.compose("restart", "restore-api", "restore-mcp", "restore-run-worker")
            ready(http)
            after = request(http, "GET", "/api/v1/runs/" + run_id, headers=support)
            assert (
                after["status"] == "completed"
                and after["output"]["request"]["receipt_id"] == final["receipt_id"]
            )
            # Withdraw ONLY the recovered copy after application completion.
            request(
                http,
                "POST",
                "/api/v1/knowledge/documents/" + fixture["document_id"] + "/withdraw",
                headers=admin,
                json={},
            )
            removed = request(http, "POST", "/api/v1/knowledge/search", headers=support, json=query)
            assert removed["matches"] == []
            citation = request(
                http,
                "GET",
                "/api/v1/knowledge/citations/" + found["matches"][0]["id"],
                headers=support,
            )
            assert citation["currently_published"] is False
            usage = request(http, "GET", f"/api/v1/runs/{run_id}/usage", headers=support)
            assert usage["model_attempts"] == 0
        source_status = self.client.sql(
            "SELECT status FROM wb_runs WHERE id='" + run_id + "';", SOURCE
        ).strip()
        assert source_status == "awaiting_input"
        source_snapshot = self.client.snapshot(SOURCE)
        assert source_snapshot == state["backup"]["logical_snapshot"]
        result = {
            "date": datetime.now(UTC).isoformat(),
            "completed": True,
            "scope": "same-host same-version local demonstration, paused workflow only",
            "backup_restore_seconds": state["backup_restore_seconds"],
            "index_rebuild_seconds": self.state["index_rebuild_seconds"],
            "restored_tables": state["backup"]["restored_table_count"],
            "backup_sql_sha256": state["backup"]["sql_sha256"],
            "source_unchanged_after_restore": True,
            "original_pause_preserved": True,
            "source_run_still_waiting": True,
            "recovered_run_completed": True,
            "application_count_for_demo_item": count,
            "simulated_receipt_event_count": receipt_count,
            "repeated_resume_keys_checked": True,
            "independent_approval_checked": True,
            "organization_isolation_checked": True,
            "source_file_hash_checked": True,
            "restored_withdrawal_checked": True,
            "restart_preserved_receipt": True,
            "actual_refund_executed": False,
            "model_provider_calls": 0,
            "image_ids": state["images"],
            "index": json.loads((self.directory / "index-proof.json").read_text()),
            "limits": [
                "Shared Docker/MySQL engine; not offsite disaster recovery",
                "No live model, production capacity, running-write crash, RTO/RPO or cutover claim",
                "Index rebuilt from restored immutable SQL chunks; no parser/chunker migration",
            ],
        }
        save(self.directory / "result.json", result)
        self.checkpoint(verified=True)
        print("Full recovery verified; sanitized result saved", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--phase", choices=["all", "resume", "stop"], default="all")
    args = parser.parse_args()
    directory = args.directory or ROOT / "work/recovery" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    )
    # Validate ownership before creating even an empty directory.
    drill = Drill(directory)
    if args.phase == "all":
        directory.mkdir(parents=True, exist_ok=False)
    elif not (directory / "state.json").is_file():
        raise ValueError("Resume/stop requires an existing owned drill directory")
    if args.phase == "stop":
        drill.compose("stop")
        return
    if drill.state.get("verified"):
        print("This drill is already verified; existing evidence and stopped services preserved")
        return
    values = dict(
        line.split("=", 1)
        for line in (ROOT / "work/docker/acceptance.env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    password = values["WORKBENCH_DOCKER_DEMO_PASSWORD"]
    print("Private recovery directory: " + str(directory), flush=True)
    start = time.monotonic()
    try:
        drill.prepare(password)
        drill.backup()
        drill.provision()
        drill.launch()
        drill.reindex()
        drill.verify(password)
        drill.checkpoint(total_seconds=time.monotonic() - start)
    finally:
        if "project" in drill.state:
            drill.compose("stop")
            print("Only recovery services stopped; database, files and index retained", flush=True)


if __name__ == "__main__":
    main()
