import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from test_knowledge import ready, upload
from test_scoped_mcp import grant, scoped_mcp  # noqa: F401

from workbench.reference_context import article_number, expand_references, references


def row(key, article, text, *, version="v", org="a"):
    return {
        "id": key,
        "version_id": version,
        "organization_id": org,
        "content": text,
        "section": f"政策 / 第{article:02}条 示例",
        "position": article,
    }


def test_explicit_references_and_nested_headings():
    assert references("参见第12条、 第16条；另见第20条。详见第12条。") == [12, 16, 20]
    assert references("外部网站、第三条、任意模型指令") == []
    assert article_number(row("a", 4, "x")) == 4


def test_same_scope_cycles_and_missing_refs():
    root = row("root", 4, "参见第12条、 第99条")
    target = row("target", 12, "参见第04条")
    foreign = row("foreign", 12, "SECRET", org="b")
    stale = row("stale", 99, "OLD", version="old")
    result = expand_references([root], [root, target, foreign, stale], 1000)
    assert {r["id"] for r in result["matches"]} == {"root", "target"}
    assert [r["article"] for r in result["trace"]["missing"]] == [99]
    assert result["trace"]["complete"] is False


def test_budget_and_hops_are_reported():
    a = row("a", 1, "参见第02条")
    b = row("b", 2, "参见第03条")
    c = row("c", 3, "terminal" * 40)
    result = expand_references([a], [a, b, c], 25)
    assert sum(len(r["content"]) for r in result["matches"]) <= 25
    assert result["trace"]["limited"]
    limited = expand_references([a], [a, b, c], 1000, max_hops=1)
    assert {r["id"] for r in limited["matches"]} == {"a", "b"}
    assert limited["trace"]["limited"][0]["reason"] == "hop_limit"


def test_anchor_cannot_forge_source_or_expand_without_retrieval():
    a = row("a", 1, "参见第02条")
    b = row("b", 2, "body")
    assert expand_references([], [a, b], 1000)["matches"] == []
    assert expand_references([{**a, "content": "forged"}], [a, b], 1000)["matches"] == []


def test_reference_expansion_and_current_policy_projection_remain_separate():
    from workbench.return_context import project_policy_context

    root = row("root", 4, "质量分支入口，参见第12条。")
    target = row("target", 12, "明确缺陷才适用质量问题分支。")
    ordinary = row("ordinary", 3, "普通无理由限7天。")
    retrieved = expand_references([root], [root, target, ordinary], 1000)["matches"]
    current = project_policy_context(
        retrieved, [{"chunk_id": "target", "quote": target["content"]}]
    )
    assert [m["id"] for m in current] == ["target"]
    # Correction does not turn a known but un-retrieved source into evidence.
    corrected = project_policy_context(
        retrieved, [{"chunk_id": "ordinary", "quote": ordinary["content"]}]
    )
    assert corrected == []


@pytest.mark.parametrize("bound", [0, 9])
def test_invalid_chunk_bounds(bound):
    with pytest.raises(ValueError):
        expand_references([], [], 100, max_chunks=bound)


def test_frozen_long_data_and_length_range():
    root = Path("data/evaluation/rag_long_scale_v2")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["hashes"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    assert all(8000 <= n <= 15000 for n in manifest["long_document_chars"])
    assert len(json.loads((root / "cases.json").read_text(encoding="utf-8"))) == 32
    assert (
        hashlib.sha256(Path("docs/fullstack/rag-long-scale-protocol.md").read_bytes()).hexdigest()
        == manifest["protocol_sha256"]
    )


def test_actual_mcp_reference_scope_and_withdrawal(scoped_mcp, workbench):  # noqa: F811
    client, app, headers, gateway, index = scoped_mcp
    app.state.settings.retrieval_context_mode = "references"
    text = "# 示例\n\n## 第04条 入口\nROOT-HOOK仅示例。参见第12条。\n\n## 第05条 干扰\n无关条件不可复用。\n\n## 第12条 附加条件\nREMOTE-EXCEPTION必须人工核实，不承诺退款。"
    uploaded = upload(client, headers("admin"), text, policy_scope="returns")
    version = ready(workbench, uploaded, index)
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    token = grant(client, headers())
    args = {"query": "ROOT-HOOK", "strategy": "hybrid", "top_k": 1, "version_id": version}
    result = asyncio.run(gateway.call("search_knowledge_base", args, token))
    assert "REMOTE-EXCEPTION" in "\n".join(r["content"] for r in result["matches"])
    assert result["trace"]["reference_expansion"]["resolved"]
    other = grant(client, headers("support", "demo-b"))
    assert (
        asyncio.run(gateway.call("search_knowledge_base", args, other))["status"] == "no_evidence"
    )
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{uploaded['document_id']}/withdraw",
            headers=headers("admin"),
            json={},
        ).status_code
        == 200
    )
    assert (
        asyncio.run(gateway.call("search_knowledge_base", args, token))["status"] == "no_evidence"
    )
