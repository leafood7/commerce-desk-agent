import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "review_module", ROOT / "scripts/rescore_rag_long_scale.py"
)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_review_separates_question_sufficiency_and_keeps_primary_labels():
    cases = json.loads(
        (ROOT / "data/evaluation/rag_long_scale_v2/cases.json").read_text(encoding="utf-8")
    )
    rubric = json.loads(
        (ROOT / "data/evaluation/rag_long_scale_review_v1/rubric.json").read_text(encoding="utf-8")
    )
    rows = [
        {
            "tier": 12,
            "arm": "anonymous",
            "case_id": "long-dev-001",
            "evidence": [{"version_id": "long-0", "content": cases[0]["required_quotes"][0]}],
            "metrics": {"necessary_coverage": 0.2, "unknown_fact_returns_policy": None},
        },
        {
            "tier": 12,
            "arm": "anonymous",
            "case_id": "long-dev-032",
            "evidence": [{"version_id": "long-0", "content": "用户自述或照片本身不等于确认缺陷"}],
            "metrics": {"necessary_coverage": None, "unknown_fact_returns_policy": True},
        },
    ]
    original = copy.deepcopy(rows)
    result = review.rescore(rows, cases, rubric)
    assert rows == original
    assert result["independent_blind_review"] is False
    assert all(r["reviewed_question_evidence_hit"] for r in result["traces"])
    assert result["traces"][0]["original_process_bundle_coverage"] == 0.2
    assert result["results"][0]["corrected_negative_cases"] == 0


def test_review_cannot_credit_wrong_version_with_identical_quote():
    cases = json.loads(
        (ROOT / "data/evaluation/rag_long_scale_v2/cases.json").read_text(encoding="utf-8")
    )
    rubric = json.loads(
        (ROOT / "data/evaluation/rag_long_scale_review_v1/rubric.json").read_text(encoding="utf-8")
    )
    rows = [
        {
            "tier": 12,
            "arm": "anonymous",
            "case_id": "long-dev-001",
            "evidence": [{"version_id": "foreign", "content": cases[0]["required_quotes"][0]}],
            "metrics": {"necessary_coverage": 0.2, "unknown_fact_returns_policy": None},
        }
    ]
    assert (
        review.rescore(rows, cases, rubric)["traces"][0]["reviewed_question_evidence_hit"] is False
    )
