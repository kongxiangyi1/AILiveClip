# -*- coding: utf-8 -*-
"""云端计费服务(FastAPI): ai-live-clip 付费改造的服务端闸口。

端点:
  POST /api/v1/resource          资源闸口: 无凭证 -> 402+Payment-Needed; 有凭证 -> 验付->履约->交付
  GET  /api/v1/orders/{out_no}   订单状态查询
  POST /api/v1/dev/mock-pay      [仅 MOCK 模式] 模拟支付成功, 返回可用凭证
  GET  /healthz                  健康检查

控制流严格对齐官方契约(interface-contract/aipay-interface-contract.md):
  402 账单 -> 验付(active/金额/订单号/资源/trade_no 未重复履约/pay_before 有效)
  -> 原子生成一次资源 -> 履约确认成功 -> 才标记完成并交付
  履约确认失败返回 502, 允许用同一 Payment-Proof 重试(不重新扣费)

运行(本地联调):
  ALIPAY_MOCK=1 uvicorn app:app --port 8787   (在 payserver/ 目录)
生产: 密钥全部走环境变量/KMS, ALIPAY_MOCK=0。
"""
import base64
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import alipay_client as alipay
from orders import DuplicateSettlement, Orders


class ResourceReq(BaseModel):
    resource_id: str = ""
    out_trade_no: str | None = None

PRICE_YUAN = alipay.normalize_amount(os.environ.get("PRICE_YUAN", "1.00")) or "1.00"
GOODS_NAME = os.environ.get("GOODS_NAME", "直播切片工具·按量付费")
BILL_DB = os.environ.get("BILL_DB", str(Path(__file__).parent / "data" / "orders.db"))

app = FastAPI(title="ai-live-clip billing", version="0.2.0")
orders = Orders(BILL_DB)


@app.on_event("startup")
def _startup_guard():
    """启动期硬校验: 配置/Mock 组合非法直接拒绝启动(官方 checklist 要求)。"""
    try:
        alipay.assert_runtime_config()
    except RuntimeError as e:
        # 不打印任何密钥原文
        raise RuntimeError(str(e)) from None


def _new_order(resource_id):
    bill, out_trade_no = alipay.build_bill(resource_id, PRICE_YUAN, GOODS_NAME)
    orders.create(out_trade_no, resource_id, bill["protocol"]["amount"],
                  pay_before=bill["protocol"]["pay_before"], goods_name=GOODS_NAME)
    return bill, out_trade_no


def _bill_response(bill, message):
    body = {"code": "Payment-Needed", "message": message,
            "out_trade_no": bill["protocol"]["out_trade_no"],
            "amount": bill["protocol"]["amount"], "currency": "CNY",
            "goods_name": bill["method"]["goods_name"]}
    return JSONResponse(status_code=402, content=body,
                        headers={"Payment-Needed": alipay.encode_payment_needed(bill)})


def _make_license(rec):
    """生成本次执行授权(业务资源)。只在 prepare_fulfillment 内被调用一次。"""
    return json.dumps({
        "ok": True,
        "token": f"PASS_{rec['out_trade_no']}",
        "out_trade_no": rec["out_trade_no"],
        "resource_id": rec["resource_id"],
        "amount": rec["amount"],
        "scope": "一次直播切片流水线执行授权",
    }, ensure_ascii=False)


def _deliver(rec, already_fulfilled=False):
    """交付成功响应: 必须同时回 Payment-Validation 头 + 非空 resource_id/content。

    官方联调成功证据(INT.A2M.DELIVERY_EVIDENCE)要求 HTTP 200 + 非空可归属资源
    + 无业务失败 + 有效 Payment-Validation, 缺一即判失败。
    """
    body = {
        "resource_id": rec["resource_id"],
        "content": rec["service_result"],
        "token": f"PASS_{rec['out_trade_no']}",
        "out_trade_no": rec["out_trade_no"],
        "trade_no": rec["trade_no"],
        "amount": rec["amount"],
        "already_fulfilled": already_fulfilled,
        "fulfillment_confirmed": rec["fulfill_status"] == "FULFILLED",
    }
    return JSONResponse(
        status_code=200, content=body,
        headers={"Payment-Validation": alipay.encode_payment_validation(
            rec["trade_no"], rec["out_trade_no"], rec["resource_id"])})


@app.post("/api/v1/resource")
def resource(req: ResourceReq,
             payment_proof: str | None = Header(default=None, alias="Payment-Proof")):
    resource_id = req.resource_id.strip()
    if not resource_id:
        raise HTTPException(400, "resource_id 必填(客户端按视频指纹生成)")

    # 1) 无凭证 -> 402 账单下发
    if not payment_proof or not payment_proof.strip():
        bill, _ = _new_order(resource_id)
        return _bill_response(bill, f"请先支付 {PRICE_YUAN} CNY 以使用本次切片服务")

    # 2) 定位订单。官方 Payment-Proof 只含 payment_proof/trade_no, 不含 out_trade_no,
    #    定位顺序: 请求体订单号 -> trade_no 反查 -> 验付返回的 out_trade_no 反查
    try:
        probe = alipay.decode_proof(payment_proof)
    except Exception:
        raise HTTPException(400, "Payment-Proof 解析失败(需 Base64URL 编码的凭证 JSON)")
    trade_no = str(probe.get("protocol", {}).get("trade_no", "")).strip()
    out_no = (req.out_trade_no or "").strip()

    rec = orders.get(out_no) if out_no else None
    if rec is None and trade_no:
        rec = orders.get_by_trade_no(trade_no)
        if rec is not None and out_no and rec["out_trade_no"] != out_no:
            raise HTTPException(409, "交易号与商户订单号不匹配(防串用)")
    if rec is not None:
        out_no = rec["out_trade_no"]
        if rec["resource_id"] != resource_id:
            raise HTTPException(403, "凭证与当前资源不匹配(防串用)")

    ph = alipay.proof_hash(payment_proof)
    if rec is not None:
        # 同一订单换了另一张凭证 -> 防重放
        if rec["proof_hash"] and rec["proof_hash"] != ph:
            raise HTTPException(409, "订单已用其他凭证结算(防重放)")
        # 资源已生成过: 幂等返回已保存结果, 或继续重试确认, 绝不重复生成
        if rec["fulfill_status"] in ("PENDING_CONFIRM", "FULFILLED"):
            return _deliver(rec, already_fulfilled=rec["fulfill_status"] == "FULFILLED")

    # 3) 验付: 接口成功只是第一步, 业务字段逐项核对
    expect_amount = rec["amount"] if rec is not None else PRICE_YUAN
    verdict = alipay.verify_proof(payment_proof, out_no or None,
                                  resource_id, expect_amount)
    if not verdict.get("ok"):
        bill, _ = _new_order(resource_id)
        return _bill_response(bill, f"验付未通过({verdict.get('reason', '凭证无效')}), 已下发新账单")
    if rec is None:
        rec = orders.get(verdict.get("out_trade_no", ""))
        if rec is None or rec["resource_id"] != resource_id:
            bill, _ = _new_order(resource_id)
            return _bill_response(bill, "订单不存在或已过期, 已下发新账单")
        out_no = rec["out_trade_no"]

    # 4) 原子进入履约: 校验金额/资源/pay_before -> 只生成一次资源
    try:
        fulfillment = orders.prepare_fulfillment(
            out_no, verdict["trade_no"], rec["amount"], rec["resource_id"],
            create_resource=lambda: _make_license(rec),
            pay_before_ok=alipay.is_future(rec["pay_before"]),
            proof_hash=ph)
    except DuplicateSettlement as e:
        raise HTTPException(409, str(e))
    if not fulfillment or fulfillment.get("state") not in ("PENDING_CONFIRM", "FULFILLED"):
        bill, _ = _new_order(resource_id)
        return _bill_response(bill, "订单不可用或账单已过期, 已下发新账单")

    rec = orders.get(out_no)
    if fulfillment["state"] == "FULFILLED":
        return _deliver(rec, already_fulfilled=True)

    # 5) 履约确认成功后才交付; 失败返回 502, 允许用同一凭证重试确认
    if not alipay.fulfillment_confirm(verdict["trade_no"]).get("ok"):
        return JSONResponse(status_code=502, content={
            "code": "FULFILLMENT_CONFIRM_FAILED",
            "message": "资源已生成但履约确认失败, 请使用同一 Payment-Proof 重试",
            "out_trade_no": out_no})
    rec = orders.mark_fulfilled(out_no, verdict["trade_no"])
    return _deliver(rec)


@app.get("/api/v1/orders/{out_trade_no}")
def order_status(out_trade_no: str):
    rec = orders.get(out_trade_no)
    if not rec:
        raise HTTPException(404, "订单不存在")
    return {"out_trade_no": rec["out_trade_no"], "amount": rec["amount"],
            "resource_id": rec["resource_id"], "order_status": rec["order_status"],
            "fulfill_status": rec["fulfill_status"], "pay_before": rec["pay_before"]}


@app.post("/api/v1/dev/mock-pay")
def mock_pay(out_trade_no: str):
    """[仅沙箱 MOCK] 模拟支付成功, 返回客户端可用的 Payment-Proof(Base64)。"""
    if not alipay.mock_enabled():
        raise HTTPException(403, "仅沙箱 MOCK 模式可用 mock-pay; 生产环境已硬禁用")
    if not orders.get(out_trade_no):
        raise HTTPException(404, "订单不存在")
    proof = {"protocol": {"payment_proof": f"MOCKPROOF_{out_trade_no}",
                          "trade_no": f"MOCK_{out_trade_no}"},
             "method": {"client_session": "mock"}}
    return {"payment_proof": base64.b64encode(json.dumps(proof).encode()).decode()}


@app.get("/healthz")
def healthz():
    return {"ok": True, "mock": alipay.mock_enabled(),
            "sandbox": alipay.is_exact_sandbox_mode(), "orders": orders.stats()}
