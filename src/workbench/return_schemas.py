"""Explicit return inputs. Eligibility is a merchant rule, never an LLM assertion."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator

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

    @field_validator("skus")
    @classmethod
    def distinct_skus(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate SKU")
        return value


class ReturnInput(Input):
    order_number: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    item_id: StrictStr = Field(min_length=1, max_length=36)
    policy_id: StrictStr = Field(min_length=1, max_length=36)
    quantity: StrictInt = Field(ge=1, le=10000)
    reason: StrictStr = Field(min_length=1, max_length=500)
    item_condition: Literal["unknown", "unused", "used"] = "unknown"

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
