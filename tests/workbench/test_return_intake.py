"""Frozen developer descriptions; extraction is a proposal, not permission."""

import hashlib
import json
from pathlib import Path

import pytest

from workbench.return_fact_checks import fact_checks, question_requirements
from workbench.return_intake import condition_candidate

DATA = Path("data/evaluation/return_intake_v1")
CONDITIONS = json.loads((DATA / "conditions.json").read_text(encoding="utf-8"))
QUESTIONS = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CONDITIONS, ids=lambda case: case["id"])
def test_frozen_condition_candidates_keep_source_and_uncertainty(case):
    result = condition_candidate(case["text"])
    assert result["candidate"] == case["candidate"]
    assert result["label"] == case["label"]
    assert result["requires_confirmation"] == case["candidate_requires_confirmation"]
    if result["source"]:
        source = result["source"]
        assert case["text"][source["start"] : source["end"]] == source["quote"]
        assert source["start"] >= result["scope_start"]


@pytest.mark.parametrize("case", QUESTIONS, ids=lambda case: case["id"])
def test_requested_facts_and_explicit_read_only(case):
    result = question_requirements(case["question"])
    assert [item["field"] for item in result["requested"]] == case["requested_facts"]
    assert result["read_only"] == case["read_only"]


def test_estimated_delivery_and_policy_snippets_do_not_prove_receipt_or_refund():
    result = fact_checks(
        "只查实际签收和实际退款金额",
        {"estimated_delivery": "tomorrow"},
        evidence=[{"id": "return-policy"}],
    )
    assert all(item["status"] == "unknown" and item["value"] is None for item in result["items"])
    actual = fact_checks("只查实际签收", {"signed_at": "2026-10-28T12:00:00"})
    assert actual["items"][0]["provenance"]["kind"] == "tool_field"


def test_intake_fixture_hashes_and_protocol_are_frozen():
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["hashes"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == digest
    assert (
        hashlib.sha256(Path("docs/fullstack/return-intake-protocol.md").read_bytes()).hexdigest()
        == manifest["protocol_sha256"]
    )
