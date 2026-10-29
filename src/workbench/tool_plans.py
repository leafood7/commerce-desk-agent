"""Model may propose bounded reads; trusted graph owns all writes and approvals."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReadPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[Literal["get_order_status", "search_knowledge_base"]] = Field(
        min_length=1, max_length=2
    )

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("Duplicate tool proposal")
        return self


def validate_read_plan(value, intent, known):
    plan = ReadPlan.model_validate(value)
    required = {
        "lookup": {"get_order_status"},
        "knowledge": {"search_knowledge_base"},
        "shipping": {"get_order_status", "search_knowledge_base"},
    }.get(intent)
    if required is None or set(plan.tools) != required:
        raise ValueError("Plan does not cover the supported task or adds unrelated tools")
    if "get_order_status" in plan.tools and not known.get("order_number"):
        raise ValueError("Order number must be explicitly supplied before tool execution")
    return plan.tools
