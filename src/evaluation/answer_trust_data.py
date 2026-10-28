"""Frozen dataset loading and strict experiment identity."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/evaluation/answer_trust"
BASELINE_REF = "571af81e15696fc278e555e39d8694ca4a051572"
VARIANTS = ("baseline", "contract", "guarded")
LAYERS = ("fixed", "mcp")
CATEGORIES = tuple(x["id"] for x in json.loads((DATA / "categories.json").read_text(encoding="utf-8")))

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def write_json(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise FileExistsError(f"Refusing to replace existing artifact: {path}")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)

def validate_frozen(root=ROOT):
    manifest = read_json(root / "data/evaluation/answer_trust/manifest.json")
    for name, expected in {**manifest["files"], **manifest["corpus"]}.items():
        if digest(root / name) != expected:
            raise ValueError(f"Frozen file changed: {name}")
    if manifest["baseline_ref"] != BASELINE_REF:
        raise ValueError("Unexpected baseline")
    return manifest

def documents(root=ROOT):
    result = {}
    for path in sorted((root / "data/knowledge").glob("*.txt")):
        lines = [x.strip() for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        for i, line in enumerate(lines[1:]):
            result[f"{path.name}-{i}"] = {"id": f"{path.name}-{i}", "source": path.name, "content": lines[0] + "\n\n" + line}
    return result

def load_cases(split="dev", ids=None, root=ROOT):
    if split not in {"dev", "holdout"}:
        raise ValueError("split must be dev or holdout")
    validate_frozen(root)
    cases = [json.loads(x) for x in (root / f"data/evaluation/answer_trust/{split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if len(cases) != 24 or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Incomplete or duplicate cases")
    corpus = documents(root)
    for case in cases:
        if case["split"] != split or case["category"] not in CATEGORIES or not 1 <= len(case["turns"]) <= 3:
            raise ValueError("Invalid case structure")
        if any(e not in corpus for e in case["evidence_ids"]):
            raise ValueError("Unknown evidence")
    if ids:
        requested = ids.split(",") if isinstance(ids, str) else list(ids)
        if len(requested) != len(set(requested)) or not set(requested) <= {c["id"] for c in cases}:
            raise ValueError("Unknown or duplicate requested case ID")
        cases = [c for c in cases if c["id"] in requested]
    if not cases:
        raise ValueError("Empty selection")
    return cases

def variant_order(index, variants=VARIANTS, repeat=1):
    offset = (index + repeat - 1) % len(variants)
    return list(variants[offset:]) + list(variants[:offset])

def candidate_snapshot(root=ROOT):
    paths = sorted((root / "src").rglob("*.py"))
    paths += sorted((root / "scripts").glob("evaluate_answer_trust.py"))
    paths += [root / "uv.lock", root / "data/evaluation/answer_trust/manifest.json"]
    return {str(p.relative_to(root)).replace("\\", "/"): digest(p) for p in paths}

def verify_candidate_lock(path, root=ROOT):
    frozen = read_json(path)
    if frozen.get("source_hashes") != candidate_snapshot(root):
        raise ValueError("Candidate changed since freeze; holdout cannot run")
    return frozen

