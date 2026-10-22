"""本地与MCP共用的只读业务响应，不依赖Agent或模型接口。"""
import json
import logging
import re
import sqlite3

ORDER_DESCRIPTION = """查询CommerceDesk订单；订单号如A1002。返回商品、支付状态、物流状态、预计送达、支付时间和承诺发货时间。
不提供签收日期/时刻、手机激活状态或实物完好情况；这些需用户提供或人工核实，不能根据订单号判断具体退货资格。"""
POLICY_DESCRIPTION = "检索商城退款、退换货、物流、会员和发票政策；其他公司信息问题也可检索确认有无依据。返回真实片段ID、来源、正文及同源补充条款。"


def order_result(order_id, lookup=None):
    if lookup is None:
        from business.order_repository import get_order_from_db
        lookup = get_order_from_db
    normalized = order_id.strip().upper()
    if re.fullmatch(r"A[0-9]+", normalized) is None:
        return "订单号格式不正确，请提供类似 A1002 的订单号。"
    try:
        order = lookup(normalized)
    except FileNotFoundError as exc:
        return str(exc)
    except sqlite3.Error:
        return "订单数据库暂时不可用，请检查本地数据库配置。"
    if order is None:
        return f"未找到订单 {normalized}，请核对订单号。"
    return json.dumps(order, ensure_ascii=False)


def policy_result(query, search=None, logger=None):
    if search is None:
        from business.knowledge import search_policies
        search = search_policies
    logger = logger or logging.getLogger(__name__)
    try:
        matches = search(query)
    except Exception:
        logger.exception("Policy retrieval failed")
        return json.dumps({"status": "error", "message": "政策检索暂时不可用，无法确认。请检查索引初始化和服务日志。"}, ensure_ascii=False)
    if not matches:
        return json.dumps({"status": "no_evidence", "message": "知识库没有找到相关政策依据。", "matches": []}, ensure_ascii=False)
    return json.dumps({"status": "found", "matches": matches}, ensure_ascii=False)
