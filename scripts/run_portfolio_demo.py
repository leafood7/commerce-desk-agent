"""Prepare fresh local demo data and replay three existing browser acceptance stories.

No model calls, downloads, database cleanup or server restart. Screenshots/reporter
stay private in work until individually reviewed for publication.
"""

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from verify_acceptance_backup import ROOT, AcceptanceDatabase

STORIES = {
    "normal_return": "return agent asks, corrects, persists, approves and simulates",
    "unknown_facts": "guided return confirms source quote and separates unknown facts",
    "branch_correction": "used item after ten days needs explicit defect branch and rechecks correction",
}


def specs(report):
    for suite in report.get("suites", []):
        yield from suite.get("specs", [])
        yield from specs(suite)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    node = shutil.which("node")
    cli = ROOT / "frontend/node_modules/@playwright/test/cli.js"
    if not args.prepare_only and (not node or not cli.is_file()):
        raise RuntimeError("Use the existing local Node/Playwright installation before replay")
    env = {
        **os.environ,
        "TEMP": r"D:\Docker\temp",
        "TMP": r"D:\Docker\temp",
        "npm_config_cache": str(ROOT / "work/cache/npm"),
        "HF_HUB_OFFLINE": "1",
    }
    client = AcceptanceDatabase()
    # Do not rely on a test-only fake-model environment variable to protect server calls.
    code = (
        "from workbench.config import WorkbenchSettings; import json; s=WorkbenchSettings(); "
        "print(json.dumps({'safe':s.agent_router_mode=='rules' and s.return_decision_mode=='rules' "
        "and not bool(s.agent_router_key and s.agent_router_key.get_secret_value())}))"
    )
    if not json.loads(client.run(["exec", "-T", "api", "python", "-c", code]))["safe"]:
        raise RuntimeError("Portfolio replay requires a model-disabled local acceptance API")
    busy = int(
        client.sql(
            "SELECT COUNT(*) FROM wb_runs WHERE status IN ('queued','running');", "commercedesk"
        )
    )
    if busy:
        raise RuntimeError("Another task is executing; defer replay instead of interrupting it")
    with httpx.Client(timeout=10, trust_env=False) as http:
        for url in ["http://127.0.0.1:18082/ready", "http://127.0.0.1:15173/"]:
            if http.get(url).status_code != 200:
                raise RuntimeError("Local acceptance API/web is not ready")
    directory = (
        ROOT
        / "work/portfolio"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8])
    )
    directory.mkdir(parents=True, exist_ok=False)
    fixture = json.loads(client.run(["exec", "-T", "api", "python", "scripts/seed_return_demo.py"]))
    content = json.dumps(fixture, ensure_ascii=False, indent=2) + "\n"
    (directory / "demo.json").write_text(content, encoding="utf-8")
    target = ROOT / "work/returns-v1/demo.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copyfile(target, directory / "previous-demo.json")
    target.write_text(content, encoding="utf-8")
    print("Fresh labelled demo data: " + str(directory / "demo.json"), flush=True)
    if args.prepare_only:
        print("Prepared only; manual demonstrations can use these fresh order/policy IDs")
        return
    values = dict(
        line.split("=", 1)
        for line in (ROOT / "work/docker/acceptance.env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    env.update(
        WORKBENCH_PORTFOLIO_SCREENSHOTS="1",
        WORKBENCH_TEST_PASSWORD=values["WORKBENCH_DOCKER_DEMO_PASSWORD"],
        WORKBENCH_TEST_URL="http://127.0.0.1:18082",
        WORKBENCH_WEB_URL="http://127.0.0.1:15173",
    )
    with (
        (directory / "playwright.json").open("wb") as stdout,
        (directory / "stderr.log").open("wb") as stderr,
    ):
        completed = subprocess.run(
            [
                node,
                str(cli),
                "test",
                "e2e/returns.spec.ts",
                "e2e/quality.spec.ts",
                "--grep",
                "|".join(STORIES.values()),
                "--workers=1",
                "--retries=0",
                "--trace=off",
                "--reporter=json",
                "--output",
                str(directory / "screenshots"),
            ],
            cwd=ROOT / "frontend",
            env=env,
            stdout=stdout,
            stderr=stderr,
            timeout=600,
        )
    raw_report = (directory / "playwright.json").read_text(encoding="utf-8")
    if not raw_report.strip():
        raise RuntimeError(
            "Browser runner did not produce a report; private stderr: "
            + str(directory / "stderr.log")
        )
    report = json.loads(raw_report)
    results = {}
    for spec in specs(report):
        key = next((k for k, title in STORIES.items() if title == spec["title"]), None)
        if key:
            result = spec["tests"][0]["results"][-1]
            results[key] = {
                "title": spec["title"],
                "status": result["status"],
                "duration_ms": result["duration"],
                "screenshots": [
                    a["path"]
                    for a in result.get("attachments", [])
                    if a["contentType"] == "image/png"
                ],
            }
    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "stories": results,
        "all_three_passed": set(results) == set(STORIES)
        and all(r["status"] == "passed" for r in results.values()),
        "new_paid_calls": 0,
        "model_disabled_preflight": True,
        "trace_and_video": "off",
        "screenshots_not_auto_published": True,
        "fresh_demo_data_added": True,
        "original_orders_not_modified": True,
        "runtime_logic_changed": False,
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if completed.returncode or not summary["all_three_passed"]:
        raise RuntimeError(
            "Portfolio replay failed; private results preserved at " + str(directory)
        )
    print("Three portfolio stories passed; private summary: " + str(directory / "summary.json"))


if __name__ == "__main__":
    main()
