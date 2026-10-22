"""统一工作台的催单与审批接口。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from contextlib import closing

from business.shipping import list_requests, run_approval, submit_request
from business.shipping import connect

router = APIRouter(prefix="/supportpilot/shipping", tags=["催单与客服审批"])


class ShippingInput(BaseModel):
    order_id: str = Field(pattern=r"^[Aa][0-9]+$", max_length=32)
    kind: Literal["normal", "expedite"] = "normal"
    reason: str = Field(default="用户要求催发货", min_length=1, max_length=500)


class DecisionInput(BaseModel):
    action: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=500)


@router.get("/requests")
def requests():
    return list_requests()


@router.post("/requests")
def create_request(body: ShippingInput):
    result = submit_request(body.order_id, body.kind, body.reason)
    if result["status"] == "pending":
        run_approval(result["request_id"])
    return result


@router.post("/requests/{request_id}/decision")
def decide(request_id: str, body: DecisionInput):
    try:
        return run_approval(request_id, {**body.model_dump(), "actor": "工作台操作"})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/orders")
def orders():
    with closing(connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM orders")]
