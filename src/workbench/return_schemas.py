"""Explicit return inputs. Eligibility is a merchant rule, never an LLM assertion."""

from typing import Annotated, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_serializer,
    model_validator,
)

from workbench.schemas import Input


class Clause(Input):
    chunk_id: StrictStr = Field(min_length=1, max_length=80)
    quote: StrictStr = Field(min_length=1, max_length=2000)


class ReturnPolicyInput(Input):
    version_id: StrictStr = Field(min_length=1, max_length=36)
    skus: list[Annotated[StrictStr, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=100
    )
    window_days: StrictInt = Field(ge=1, le=365)
    requires_unused: StrictBool
    scope_clause: Clause
    window_clause: Clause
    condition_clause: Clause
    administrator_verified: Literal[True]
    return_basis: Literal["no_reason", "quality_issue"] = "no_reason"
    quality_requirement: Literal["recorded", "passed", "defect_confirmed"] = "recorded"
    quality_clause: Clause | None = None
    required_facts: list[Literal["activation_status", "member_entitlement", "quality_verdict"]] = (
        Field(default_factory=list, max_length=3)
    )

    @field_validator("skus")
    @classmethod
    def distinct_skus(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate SKU")
        return value

    @field_validator("required_facts")
    @classmethod
    def distinct_facts(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate required fact")
        return value

    @model_validator(mode="after")
    def branch_contract(self):
        if self.return_basis == "quality_issue":
            if (
                self.requires_unused
                or "quality_verdict" not in self.required_facts
                or self.quality_requirement != "defect_confirmed"
                or self.quality_clause is None
            ):
                raise ValueError(
                    "Quality-issue rules require explicit defect evidence and cannot require unused"
                )
        elif self.quality_requirement == "defect_confirmed":
            raise ValueError("Defect-confirmed requirement belongs to quality-issue branch")
        return self


class ReturnInput(Input):
    order_number: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    item_id: StrictStr = Field(min_length=1, max_length=36)
    policy_id: StrictStr = Field(min_length=1, max_length=36)
    quantity: StrictInt = Field(ge=1, le=10000)
    reason: StrictStr = Field(min_length=1, max_length=500)
    item_condition: Literal["unknown", "unused", "used"] = "unknown"
    return_basis: Literal["no_reason", "quality_issue"] = "no_reason"

    @model_serializer(mode="wrap")
    def compatibility_payload(self, handler):
        data = handler(self)
        if self.return_basis == "no_reason":
            data.pop("return_basis", None)
        return data

    @field_validator("reason")
    @classmethod
    def reason_nonblank(cls, value):
        if not value.strip():
            raise ValueError("Reason is blank")
        return value.strip()


class ReturnRevision(Input):
    expected_revision: StrictInt = Field(ge=1)


class ReturnConfirm(ReturnRevision):
    preview_hash: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")


class ReturnCorrection(ReturnRevision):
    application: ReturnInput


class ReturnDecision(ReturnRevision):
    action: Literal["approve", "reject"]
    reason: StrictStr = Field(min_length=1, max_length=500)


class ReturnToolInput(ReturnInput):
    idempotency_key: StrictStr = Field(min_length=1, max_length=128)


class ReturnStatusInput(Input):
    request_id: StrictStr = Field(min_length=1, max_length=36)


class ReturnConfirmTool(ReturnConfirm, ReturnStatusInput):
    pass


class ReturnSubmitTool(ReturnRevision, ReturnStatusInput):
    pass
