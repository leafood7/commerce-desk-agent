"""统一工作台中的催单申请与人工审批。"""


import httpx
import streamlit as st


def draw_shipping_console(base_url, token=None):
    with st.expander("催单申请与人工审批", expanded=False):
        st.caption("工单仅保存在本地，不会通知外部仓库。")
        headers = {"Authorization": "Bearer " + token} if token else {}
        url = base_url.rstrip("/") + "/supportpilot/shipping/requests"
        try:
            with st.form("shipping_request"):
                owned = httpx.get(base_url.rstrip("/") + "/supportpilot/shipping/orders", headers=headers, timeout=10)
                owned.raise_for_status()
                order_id = st.selectbox("订单号", [row["order_id"] for row in owned.json()])
                kind = st.selectbox("申请类型", ["normal", "expedite"],
                                    format_func=lambda value: "普通催单（直接登记）" if value == "normal" else "加急处理（需要审批）")
                reason = st.text_input("申请原因", value="用户要求催发货")
                submitted = st.form_submit_button("提交申请")
            if submitted:
                response = httpx.post(url, json={"order_id": order_id, "kind": kind, "reason": reason}, headers=headers, timeout=20)
                response.raise_for_status()
                result = response.json()
                st.info(result.get("result_message", result.get("message")))
            st.button("刷新申请列表", key="refresh_shipping")
            response = httpx.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            rows = response.json()
            if not rows:
                st.write("暂无催单记录。")
            for row in rows:
                kind_label = {"normal": "普通催单", "expedite": "加急申请"}[row["kind"]]
                status_label = {"pending": "待审批", "completed": "已登记工单", "rejected": "已拒绝", "invalidated": "已失效"}[row["status"]]
                st.markdown(f"**{row['order_id']} · {kind_label} · {status_label}**")
                st.write(f"申请编号：{row['request_id']}；承诺发货时间：{row['promised_ship_at']}")
                st.write(f"当前物流：{row['shipping_status']}；申请原因：{row['reason']}")
                st.write(row["result_message"])
                if row.get("decided_by"):
                    st.write("处理记录：" + row["decided_by"])
                if row.get("decision_reason"):
                    st.write("审批备注：" + row["decision_reason"])
                if row["status"] == "pending":
                    with st.form("decision_" + row["request_id"]):
                        note = st.text_input("审批备注 / 拒绝原因")
                        approve = st.form_submit_button("批准加急")
                        reject = st.form_submit_button("拒绝申请")
                    if approve or reject:
                        result = httpx.post(url + "/" + row["request_id"] + "/decision",
                                            json={"action": "approve" if approve else "reject", "reason": note},
                                            headers=headers, timeout=20)
                        result.raise_for_status()
                        st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"催单接口暂不可用：{exc}")
