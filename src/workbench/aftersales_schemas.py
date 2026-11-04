from typing import Literal

from pydantic import Field, StrictInt, model_validator

from workbench.policy_audience import PolicyAudience
from workbench.schemas import Input


class FlowInput(Input):
    query: str = Field(min_length=1, max_length=1200)
    order_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    shipping_kind: Literal["normal", "expedite"] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.query.strip() or (self.reason is not None and not self.reason.strip()):
            raise ValueError("Nonblank query/reason required")
        return self


class FlowResume(Input):
    action: Literal[
        "supply",
        "correct",
        "confirm",
        "refresh",
        "retry",
        "cancel",
        "accept_condition",
        "choose_task",
        "choose_fact",
    ]
    audience: PolicyAudience | None = None
    message: str | None = Field(default=None, min_length=1, max_length=1200)
    pause_id: str | None = Field(default=None, min_length=1, max_length=128)
    order_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    shipping_kind: Literal["normal", "expedite"] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    return_item_id: str | None = Field(default=None, min_length=1, max_length=36)
    return_quantity: StrictInt | None = Field(default=None, ge=1, le=10000)
    return_condition: Literal["unknown", "unused", "used"] | None = None
    return_policy_id: str | None = Field(default=None, min_length=1, max_length=36)
    return_description: str | None = Field(default=None, max_length=500)
    return_basis: Literal["no_reason", "quality_issue"] | None = None
    return_fact: (
        Literal[
            "signed_at",
            "refund_amount",
            "bank_received",
            "activation_status",
            "member_entitlement",
            "quality_verdict",
            "coupon_reissue_amount",
        ]
        | None
    ) = None
    return_task: Literal["policy", "facts", "eligibility", "application"] | None = None
    condition_proposal_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def meaningful(self):
        if self.message is not None and not self.message.strip():
            raise ValueError("Message must be nonblank")
        if self.message is not None and self.action not in {
            "supply", "correct", "confirm", "cancel", "accept_condition"
        }:
            raise ValueError("Message is not valid for this action")
        fields = (
            self.audience,
            self.order_number,
            self.shipping_kind,
            self.reason,
            self.return_item_id,
            self.return_quantity,
            self.return_condition,
            self.return_policy_id,
            self.return_description,
            self.return_basis,
        )
        if self.action == "choose_fact":
            if (
                self.return_fact is None
                or self.return_task is not None
                or self.message is not None
                or any(value is not None for value in fields)
            ):
                raise ValueError("Fact choice requires only an actual field selector")
        elif self.return_fact is not None:
            raise ValueError("Fact selector requires the fact clarification action")
        elif self.action == "choose_task":
            if (
                self.return_task is None
                or self.message is not None
                or any(value is not None for value in fields)
            ):
                raise ValueError("Task choice requires only its explicit purpose")
        elif self.return_task is not None:
            raise ValueError("Task purpose cannot change during confirmation")
        elif self.action in {"supply", "correct"}:
            if (
                self.return_task is None
                and not any(value is not None for value in fields)
                and self.message is None
            ):
                raise ValueError("Supply/correction requires explicit fields")
        elif any(value is not None for value in fields):
            raise ValueError("Confirmation/refresh must not modify the reviewed fields")
        if self.audience is not None and self.action not in {"supply", "correct"}:
            raise ValueError("Policy scope can only be supplied during clarification")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("Reason must be nonblank")
        if (self.action == "accept_condition") != (self.condition_proposal_hash is not None):
            raise ValueError("Condition acceptance requires only its proposal hash")
        return self
