"""催单登记与加急审批；所有执行条件都由程序检查。"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from business.order_repository import get_database_path


def utc_now():
    return datetime.now(timezone.utc)


def eligibility(order, kind, now):
    if order is None:
        return "订单不存在。"
    if order["payment_status"] != "已付款":
        return "订单尚未付款，不能催发货。"
    if order["shipping_status"] != "等待仓库发货":
        return "订单已发货或已签收，无需催发货。"
    if kind == "expedite":
        promised = order["promised_ship_at"]
        if not promised:
            return "订单缺少承诺发货时间，请人工核实。"
        try:
            deadline = datetime.fromisoformat(promised)
            if deadline.tzinfo is None:
                return "承诺发货时间缺少时区，请人工核实。"
        except ValueError:
            return "承诺发货时间格式不正确，请人工核实。"
        if now <= deadline:
            return "尚未超过承诺发货时间，可以登记普通催单，暂不受理加急申请。"
    return None


def connect(database=None):
    path = get_database_path(database)
    if not path.is_file():
        raise FileNotFoundError("请先运行 scripts/init_demo_orders.py 初始化订单。")
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def submit_request(order_id, kind="normal", reason="用户要求催发货", database=None, now=None):
    if kind not in {"normal", "expedite"}:
        raise ValueError("仅支持 normal（普通催单）或 expedite（加急申请）。")
    now = now or utc_now()
    with closing(connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id.strip().upper(),)).fetchone()
        error = eligibility(order, kind, now)
        if error:
            return {"status": "ineligible", "message": error}
        previous = db.execute("SELECT * FROM shipping_requests WHERE order_id=? AND kind=?",
                              (order["order_id"], kind)).fetchone()
        if previous:
            return dict(previous)
        request_id = str(uuid4())
        ticket_id = "T-" + request_id[:12] if kind == "normal" else None
        status = "completed" if kind == "normal" else "pending"
        message = (f"已登记普通催单 {ticket_id}，尚未通知外部仓库。" if ticket_id
                   else "加急申请已提交，等待客服审批，尚未创建加急工单。")
        # 用户申请做什么、处理到哪一步
        db.execute("""INSERT INTO shipping_requests
            (request_id,order_id,kind,reason,status,created_at,ticket_id,result_message)
            VALUES (?,?,?,?,?,?,?,?)""",
            (request_id, order["order_id"], kind, reason, status, now.isoformat(), ticket_id, message))
        if ticket_id:
            # 已经正式登记的工单
            db.execute("""INSERT INTO shipping_tickets
                (ticket_id, request_id, order_id, kind, created_at) VALUES (?,?,?,?,?)""",
                (ticket_id, request_id, order["order_id"], kind, now.isoformat()))
        return dict(db.execute("SELECT * FROM shipping_requests WHERE request_id=?", (request_id,)).fetchone())  #程序把刚写入的申请查出来，转成字典返回


def list_requests(database=None):
    with closing(connect(database)) as db:
        return [dict(row) for row in db.execute("""SELECT r.*,o.shipping_status,o.promised_ship_at
            FROM shipping_requests r JOIN orders o ON o.order_id=r.order_id
            ORDER BY r.created_at DESC""")]


def execute_decision(request_id, decision, database=None, now=None):
    if decision.get("action") not in {"approve", "reject"}:
        raise ValueError("审批动作必须是 approve 或 reject。")
    now = now or utc_now()
    with closing(connect(database)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM shipping_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("申请不存在。")
        if row["status"] != "pending":
            return dict(row)
        ticket_id = None
        if decision["action"] == "reject":
            status, message = "rejected", "客服已拒绝加急申请，未创建加急工单。"
        else:
            order = db.execute("SELECT * FROM orders WHERE order_id=?", (row["order_id"],)).fetchone()
            error = eligibility(order, "expedite", now)
            status = "invalidated" if error else "completed"
            ticket_id = None if error else "T-" + request_id[:12]
            message = error or f"已创建加急工单 {ticket_id}，尚未通知外部仓库。"
        db.execute("""UPDATE shipping_requests SET status=?, decided_at=?, decision_reason=?,
            ticket_id=?,result_message=?,decided_by=? WHERE request_id=?""",
            (status, now.isoformat(), decision.get("reason", ""), ticket_id, message, decision.get("actor"), request_id))
        if ticket_id:
            db.execute("""INSERT INTO shipping_tickets
                (ticket_id, request_id, order_id, kind, created_at) VALUES (?,?,?,?,?)""",
                (ticket_id, request_id, row["order_id"], row["kind"], now.isoformat()))
        return dict(db.execute("SELECT * FROM shipping_requests WHERE request_id=?", (request_id,)).fetchone())


class ApprovalState(TypedDict):
    request_id: str
    result: dict


def approval_graph(database, saver):   #这张图只有一个节点：START → review → END
    def review(state):
        decision = interrupt({"request_id": state["request_id"], "actions": ["approve", "reject"]})
        return {"result": execute_decision(state["request_id"], decision, database)}

    graph = StateGraph(ApprovalState)
    graph.add_node("review", review)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=saver)


def run_approval(request_id, decision=None, database=None):
    with closing(connect(database)) as db:
        row = db.execute("SELECT * FROM shipping_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("申请不存在。")
        if row["kind"] != "expedite":
            return dict(row)
    path = get_database_path(database)
    with SqliteSaver.from_conn_string(str(path.with_name(path.stem + "-approvals.sqlite3"))) as saver:
        graph = approval_graph(database, saver)
        config = {"configurable": {"thread_id": request_id}}
        state = graph.get_state(config)
        if not state.values:
            graph.invoke({"request_id": request_id}, config)
        if decision is not None:
            state = graph.get_state(config)   #读取这条申请保存的流程状态
            if state.next:  #检查流程是否还有等待执行的步骤
                graph.invoke(Command(resume=decision), config)  #把人工决定交给之前暂停的流程
        with closing(connect(database)) as db:
            row = db.execute("SELECT * FROM shipping_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("申请不存在。")
            return dict(row)


def submit_shipping(order_id, kind="normal", reason="用户要求催发货"):
    result = submit_request(order_id, kind, reason)
    if result["status"] == "pending":    #申请已经保存到数据库，等待审批；加急工单还没创建。
        run_approval(result["request_id"])
    return json.dumps(result, ensure_ascii=False)
