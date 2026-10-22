"""协议测试专用独立进程；生产Server不提供测试模式开关。"""
from tool_protocol.server import build_server

MATCHES = [{"id": "coupon_scope_policy.txt-0", "source": "coupon_scope_policy.txt", "content": "店铺限定券不能跨店使用。",
            "rerank_score": 3.5, "context_chunks": [{"id": "coupon_scope_policy.txt-1", "source": "coupon_scope_policy.txt", "content": "地区限制需重新核实。"}]}]


def lookup(order_id):
    if order_id == "A1002":
        return {"order_id": order_id, "product_name": "协议测试商品", "payment_status": "已付款", "shipping_status": "运输中", "estimated_delivery": "2026-10-25"}
    return None


def search(query):
    if query == "large":
        return [{"id": "large-0", "source": "large.txt", "content": "中文政策限制" * 4000}]
    if query == "no_evidence":
        return []
    if query == "error":
        raise RuntimeError("fixture retrieval unavailable")
    return MATCHES


if __name__ == "__main__":
    build_server(lookup=lookup, search=search).run(transport="stdio")
