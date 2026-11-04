"""Explicit fulfillment inputs. Amounts and state transitions are never model-determined."""

from typing import Literal

from pydantic import Field, StrictStr

from workbench.schemas import Input


class RegisterTracking(Input):
    fulfillment_id: StrictStr = Field(min_length=1, max_length=36)
    carrier: StrictStr = Field(min_length=1, max_length=64)
    tracking_number: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


class ConfirmReceipt(Input):
    fulfillment_id: StrictStr = Field(min_length=1, max_length=36)


class InspectGoods(Input):
    fulfillment_id: StrictStr = Field(min_length=1, max_length=36)
    result: Literal["passed", "failed"]
    remark: StrictStr = Field(default="", max_length=500)


class InitiateRefund(Input):
    fulfillment_id: StrictStr = Field(min_length=1, max_length=36)
    idempotency_key: StrictStr = Field(min_length=1, max_length=128)
    # Demonstration-only knobs to exercise the mock refund service deterministically.
    outcome: Literal["succeeded", "failed"] = "succeeded"
    timeout: bool = False


class FulfillmentLookup(Input):
    fulfillment_id: StrictStr = Field(min_length=1, max_length=36)