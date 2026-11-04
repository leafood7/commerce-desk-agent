"""Structured policy rule input and conflict adjudication contracts."""

from datetime import date
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from workbench.schemas import Input


class PolicyRuleInput(Input):
    policy_id: StrictStr = Field(min_length=1, max_length=36)
    version_id: StrictStr | None = Field(default=None, max_length=36)
    version_number: StrictInt | None = Field(default=None, ge=1)
    sku: StrictStr = Field(min_length=1, max_length=64)
    return_basis: Literal["no_reason", "quality_issue", "unspecified"] = "no_reason"
    sales_channel: StrictStr = Field(min_length=1, max_length=64)
    effective_from: date
    effective_until: date | None = None
    window_days: StrictInt = Field(ge=1, le=365)
    requires_unused: StrictBool
    priority: StrictInt | None = None
    source_refs: list[StrictStr] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_window(self):
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not precede effective_from")
        return self


class CheckConflicts(Input):
    rules: list[PolicyRuleInput] = Field(default_factory=list, max_length=50)
    version_ids: list[StrictStr] = Field(default_factory=list, max_length=20)


class ResolveConflict(Input):
    decision: Literal["preferred_rule", "no_conflict"]
    preferred_rule_id: StrictStr | None = Field(default=None, max_length=120)
    note: StrictStr = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def decision_contract(self):
        if self.decision == "preferred_rule" and not self.preferred_rule_id:
            raise ValueError("preferred_rule_id is required when choosing a rule")
        if self.decision == "no_conflict" and self.preferred_rule_id:
            raise ValueError("preferred_rule_id is not allowed when confirming no conflict")
        return self