"""Read-only source/schema proof for the local inspection demonstration."""

import hashlib
import json
from pathlib import Path

import httpx
from verify_acceptance_backup import AcceptanceDatabase

ROOT = Path(__file__).resolve().parents[1]


def main():
    paths = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "src/workbench").glob("*.py")
    )
    paths.append("migrations/versions/0009_quality_return_basis.py")
    host = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    client = AcceptanceDatabase()
    proof = {"host": host, "services": {}}
    code = (
        "import pathlib,hashlib,json; paths="
        + repr(paths)
        + "; print(json.dumps({p:hashlib.sha256(pathlib.Path('/app',p).read_bytes()).hexdigest() for p in paths}))"
    )
    for service in ("api", "mcp", "run-worker"):
        found = json.loads(client.run(["exec", "-T", service, "python", "-c", code]))
        if found != host:
            raise RuntimeError("Running source differs: " + service)
        proof["services"][service] = {
            "matches": True,
            "source_files": len(paths) - 1,
            "migration_matches": True,
        }
    code = "from workbench.config import WorkbenchSettings; from workbench.database import Database; from sqlalchemy import text,inspect; import json; s=WorkbenchSettings(); d=Database(s); c=d.engine.connect(); print(json.dumps({'schema':c.scalar(text('SELECT version_num FROM alembic_version')),'tables':len(inspect(d.engine).get_table_names()),'agent_router_mode':s.agent_router_mode,'return_decision_mode':s.return_decision_mode,'model_key_present':bool(s.agent_router_key and s.agent_router_key.get_secret_value())})); c.close(); d.close()"
    runtime = json.loads(client.run(["exec", "-T", "api", "python", "-c", code]))
    if runtime != {
        "schema": "0009_quality_return_basis",
        "tables": 34,
        "agent_router_mode": "rules",
        "return_decision_mode": "rules",
        "model_key_present": False,
    }:
        raise RuntimeError("Unexpected runtime settings")
    proof["runtime"] = runtime
    with httpx.Client(trust_env=False, timeout=10) as http:
        page = http.get("http://127.0.0.1:15173/")
        page.raise_for_status()
        digest = hashlib.sha256(page.content).hexdigest()
        if digest != hashlib.sha256((ROOT / "frontend/dist/index.html").read_bytes()).hexdigest():
            raise RuntimeError("Browser asset differs from host build")
        proof["web_index_sha256"] = digest
    target = ROOT / "docs/fullstack/quality-return-evaluation/image-proof.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in proof.items() if k != "host"}, indent=2))


if __name__ == "__main__":
    main()
