from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    field_validator,
    model_validator,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("order_number", mode="before", check_fields=False)
    @classmethod
    def normalize_order(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class LoginInput(Input):
    organization: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: SecretStr = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower()


class UserInput(Input):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal["admin", "support", "approver"]
    password: SecretStr = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower()


class ConversationInput(Input):
    title: str = Field(default="新的订单会话", min_length=1, max_length=200)


class RunInput(Input):
    kind: Literal["order_lookup", "knowledge_search", "aftersales"] = "order_lookup"
    order_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    query: str | None = Field(default=None, min_length=1, max_length=1200)
    policy_scope: (
        Literal["unknown", "general", "goods_coupon", "freight_coupon", "returns", "shipping"]
        | None
    ) = None
    shipping_kind: Literal["normal", "expedite"] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    return_policy_id: str | None = Field(default=None, min_length=1, max_length=36)
    return_item_id: str | None = Field(default=None, min_length=1, max_length=36)
    return_quantity: StrictInt | None = Field(default=None, ge=1, le=10000)
    return_condition: Literal["unknown", "unused", "used"] | None = None
    return_description: str | None = Field(default=None, max_length=500)
    return_basis: Literal["no_reason", "quality_issue"] | None = None

    @model_validator(mode="after")
    def task_fields(self):
        if self.kind == "aftersales":
            if not self.return_policy_id and any(
                v is not None
                for v in (
                    self.return_item_id,
                    self.return_quantity,
                    self.return_condition,
                    self.return_description,
                    self.return_basis,
                )
            ):
                raise ValueError("Return fields require an explicit return policy")
            if self.return_policy_id and self.shipping_kind:
                raise ValueError("Return and shipping workflows cannot be mixed")
            if not self.query or not self.query.strip() or self.policy_scope:
                raise ValueError("After-sales requires a query and server-controlled policy scope")
            if self.reason is not None and not self.reason.strip():
                raise ValueError("Reason must be nonblank")
            return self
        if (
            self.shipping_kind
            or self.reason
            or any(
                v is not None
                for v in (
                    self.return_policy_id,
                    self.return_item_id,
                    self.return_quantity,
                    self.return_condition,
                    self.return_description,
                    self.return_basis,
                )
            )
        ):
            raise ValueError("Shipping fields require after-sales workflow")
        if self.kind == "order_lookup" and (
            not self.order_number or self.query or self.policy_scope
        ):
            raise ValueError("Order lookup requires only an order number")
        if self.kind == "knowledge_search" and (
            not self.query or not self.query.strip() or self.order_number
        ):
            raise ValueError("Policy search requires a nonblank query")
        return self


class ShippingInput(Input):
    order_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    kind: Literal["normal", "expedite"] = "normal"
    reason: str = Field(default="用户要求催发货", min_length=1, max_length=500)


class DecisionInput(Input):
    action: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=500)
