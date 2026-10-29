import pytest

from workbench.evidence_context import expand_context
from workbench.tool_plans import validate_read_plan


@pytest.mark.parametrize(
    "tools",
    [
        ["request_shipping"],
        ["approve_shipping"],
        ["get_order_status", "get_order_status"],
        ["get_order_status", "search_knowledge_base", "get_shipping_status"],
        [],
    ],
)
def test_model_cannot_propose_write_duplicate_or_unbounded_plan(tools):
    with pytest.raises(ValueError):
        validate_read_plan({"tools": tools}, "shipping", {"order_number": "A1001"})


def test_model_cannot_inject_parameters_or_skip_required_reads():
    with pytest.raises(ValueError):
        validate_read_plan(
            {"tools": ["get_order_status"], "arguments": {"order_number": "B2001"}},
            "lookup",
            {"order_number": "A1001"},
        )
    with pytest.raises(ValueError):
        validate_read_plan(
            {"tools": ["search_knowledge_base"]}, "shipping", {"order_number": "A1001"}
        )
    with pytest.raises(ValueError):
        validate_read_plan({"tools": ["get_order_status"]}, "lookup", {})
    assert validate_read_plan(
        {"tools": ["search_knowledge_base", "get_order_status"]},
        "shipping",
        {"order_number": "A1001"},
    ) == ["search_knowledge_base", "get_order_status"]


def row(id, position, section="return", version="v1", organization="a"):
    return {
        "id": id,
        "position": position,
        "section": section,
        "version_id": version,
        "organization_id": organization,
        "content": id * 10,
    }


def test_section_expansion_keeps_conditions_but_excludes_other_chapters_and_tenants():
    anchor = row("a", 2)
    valid = row("b", 4)
    other = row("c", 3, section="freight")
    foreign = row("d", 1, organization="b")
    previous = row("e", 1, version="old")
    result = expand_context([anchor], [anchor, valid, other, foreign, previous], 100, "section")
    assert [item["id"] for item in result] == ["a", "b"]
    assert result[1]["context_of"] == "a"


def test_context_budget_is_hard_and_sources_not_duplicated():
    a, b = row("a", 1), row("b", 2)
    result = expand_context([a, a], [a, b], 15, "section")
    assert [item["id"] for item in result] == ["a"]
    assert sum(len(x["content"]) for x in result) <= 15
