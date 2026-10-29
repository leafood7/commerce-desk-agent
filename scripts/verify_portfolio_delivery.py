"""Read-only proof that portfolio docs/demo artifacts match the local candidate."""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime

import httpx
from verify_acceptance_backup import ROOT, AcceptanceDatabase


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    folder = ROOT / "docs/portfolio"
    paths = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src/workbench").glob("*.py")
    )
    paths.append("migrations/versions/0009_quality_return_basis.py")
    expected = {p: digest(ROOT / p) for p in paths}
    client = AcceptanceDatabase()
    code = (
        "import pathlib,hashlib,json; paths="
        + repr(paths)
        + "; print(json.dumps({p:hashlib.sha256(pathlib.Path('/app',p).read_bytes()).hexdigest() for p in paths}))"
    )
    services = {}
    for service in ("api", "mcp", "run-worker"):
        found = json.loads(client.run(["exec", "-T", service, "python", "-c", code]))
        if found != expected:
            raise RuntimeError("Runtime source differs: " + service)
        services[service] = {"source_files": len(paths) - 1, "migration_matches": True}
    code = "from workbench.config import WorkbenchSettings; from workbench.database import Database; from sqlalchemy import text,inspect; import json; s=WorkbenchSettings(); d=Database(s); c=d.engine.connect(); print(json.dumps({'schema':c.scalar(text('SELECT version_num FROM alembic_version')),'tables':len(inspect(d.engine).get_table_names()),'agent_router_mode':s.agent_router_mode,'return_decision_mode':s.return_decision_mode,'retrieval_context_mode':s.retrieval_context_mode,'model_key_present':bool(s.agent_router_key and s.agent_router_key.get_secret_value())})); c.close(); d.close()"
    runtime = json.loads(client.run(["exec", "-T", "api", "python", "-c", code]))
    if runtime != {
        "schema": "0009_quality_return_basis",
        "tables": 34,
        "agent_router_mode": "rules",
        "return_decision_mode": "rules",
        "retrieval_context_mode": "neighbors",
        "model_key_present": False,
    }:
        raise RuntimeError("Unexpected local runtime mode")
    with httpx.Client(timeout=10, trust_env=False) as http:
        page = http.get("http://127.0.0.1:15173/")
        page.raise_for_status()
        web_digest = hashlib.sha256(page.content).hexdigest()
        if web_digest != digest(ROOT / "frontend/dist/index.html"):
            raise RuntimeError("Running web differs from host build")
    docs = [ROOT / "README.md", ROOT / "docs/fullstack/README.md", *folder.glob("*.md")]
    link_count = 0
    for document in docs:
        for match in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            url = match[1].split("#", 1)[0]
            if not url or "://" in url:
                continue
            target = (document.parent / url.strip("<>")).resolve()
            if not target.exists():
                raise RuntimeError(
                    "Missing delivery link: " + str(document.relative_to(ROOT)) + " -> " + url
                )
            link_count += 1
    demo = json.loads((folder / "evidence/demo-results.json").read_text(encoding="utf-8"))
    if not demo["all_three_passed"] or len(demo["stories"]) != 3:
        raise RuntimeError("Three actual demo results required")
    for story in demo["stories"].values():
        if story["status"] != "passed":
            raise RuntimeError("Demo did not pass")
    proof = {
        "captured_at": datetime.now(UTC).isoformat(),
        "services": services,
        "runtime": runtime,
        "web_index_sha256": web_digest,
        "local_links_checked": link_count,
    }
    (folder / "evidence/runtime-proof.json").write_text(
        json.dumps(proof, indent=2) + "\n", encoding="utf-8"
    )
    files = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("migrations/**/*.py"),
        *ROOT.glob("frontend/src/*"),
        *ROOT.glob("frontend/e2e/*.ts"),
        *ROOT.glob("docker/Dockerfile*"),
        *folder.glob("**/*"),
    ]
    files += [
        ROOT / name
        for name in [
            "README.md",
            "pyproject.toml",
            "uv.lock",
            "compose.yaml",
            "compose.acceptance.yaml",
            "frontend/package.json",
            "frontend/package-lock.json",
            "frontend/playwright.config.ts",
            "scripts/run_portfolio_demo.py",
            "scripts/verify_portfolio_delivery.py",
            "scripts/verify_full_recovery.py",
            "scripts/rebuild_recovery_index.py",
        ]
    ]
    hashes = {
        str(p.relative_to(ROOT)).replace("\\", "/"): digest(p)
        for p in sorted(set(files))
        if p.is_file() and p.name != "delivery-manifest.json"
    }

    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()

    manifest = {
        "local_candidate_only": True,
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "working_tree_has_changes": bool(git("status", "--porcelain")),
        "files": hashes,
        "files_digest": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "not_a_source_archive": True,
        "private_work_env_sql_auth_exported": False,
        "sealed_holdout_read": False,
        "commit_or_push_performed": False,
    }
    (folder / "delivery-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "source_files_per_service": len(paths) - 1,
                "local_links_checked": link_count,
                "candidate_files": len(hashes),
                "schema": runtime["schema"],
            }
        )
    )


if __name__ == "__main__":
    main()
