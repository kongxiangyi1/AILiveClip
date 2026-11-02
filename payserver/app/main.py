"""AI 按量付费（A2M / 402）服务端：ai-live-clip 买断授权签发。

端点（对齐 scripts/payment_gate.py 客户端契约）：
  POST /api/license
    - 无 Payment-Proof：返回 402 + Payment-Needed（Base64URL 账单）
    - 带 Payment-Proof：验付 -> 幂等生成买断授权码(LIVECLIP1.*) -> 履约确认 -> 200
  GET  /api/license/status?out_trade_no=  查询订单状态（排障）
  GET  /api/orders                         导出全部订单（供云端控制台同步；受 sharecode 网关保护）
  GET  /health
  /console/                               商户云端控制台（静态页，使用 WorkBuddy Cloud SDK）

运行：uvicorn app.main:app --host 0.0.0.0 --port 8787
"""
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import config, crypto_sign, orders
from .alipay import confirm_fulfillment, verify_payment
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="ai-live-clip billing service", version="1.0.0")

# 商户云端控制台静态资源（CDN 加载 WorkBuddy Cloud SDK，使用云服务 Database/Auth/LLM 模块）
_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)
os.makedirs(_STATIC_DIR, exist_ok=True)
app.mount("/console", StaticFiles(directory=_STATIC_DIR, html=True), name="console")


# ----------------------- 工具 -----------------------

def _normalize_amount(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
        return None
    return f"{Decimal(text):.2f}"


def _amounts_equal(left, right):
    return _normalize_amount(left) == _normalize_amount(right)


def _is_future(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() > time.time()
    except (TypeError, ValueError):
        return False


def _build_payment_needed(out_trade_no, amount, resource_id, goods_name, pay_before):
    seller_signature = crypto_sign.seller_sign(
        {
            "amount": amount,
            "currency": "CNY",
            "goods_name": goods_name,
            "out_trade_no": out_trade_no,
            "pay_before": pay_before,
            "resource_id": resource_id,
            "seller_id": config.SELLER_ID,
            "service_id": config.SERVICE_ID,
        },
        config.APP_PRIVATE_KEY,
    )
    payment_needed = {
        "protocol": {
            "out_trade_no": out_trade_no,
            "amount": amount,
            "currency": "CNY",
            "resource_id": resource_id,
            "pay_before": pay_before,
            "seller_signature": seller_signature,
            "seller_sign_type": "RSA2",
            "seller_unique_id": config.SELLER_ID,
            "service_id": config.SERVICE_ID,
        },
        "method": {
            "seller_name": config.SELLER_NAME,
            "seller_id": config.SELLER_ID,
            "seller_app_id": config.APP_ID,
            "goods_name": goods_name,
            "seller_unique_id_key": "seller_id",
            "service_id": config.SERVICE_ID,
        },
    }
    return crypto_sign.b64url_encode(json.dumps(payment_needed, ensure_ascii=False))


def _issue_402(machine_code: str):
    out_trade_no = f"LC_{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"
    amount = config.PRICE_CNY
    currency = "CNY"
    resource_id = config.RESOURCE_PATH
    goods_name = config.GOODS_NAME
    pay_before = (datetime.now(timezone.utc).astimezone() + timedelta(minutes=30)).isoformat()

    if not (config.APP_PRIVATE_KEY and config.SELLER_ID and config.APP_ID):
        return JSONResponse(
            {"code": "CONFIG_ERROR", "message": "服务端支付宝配置缺失（沙箱未配置）"},
            status_code=500,
        )

    encoded = _build_payment_needed(out_trade_no, amount, resource_id, goods_name, pay_before)
    orders.create_pending(
        {
            "out_trade_no": out_trade_no,
            "amount": _normalize_amount(amount),
            "currency": currency,
            "resource_id": resource_id,
            "goods_name": goods_name,
            "pay_before": pay_before,
            "machine_code": machine_code or "*",
        }
    )
    body = {
        "code": "Payment-Needed",
        "message": "需要支付",
        "out_trade_no": out_trade_no,
        "amount": amount,
        "currency": currency,
        "goods_name": goods_name,
    }
    return JSONResponse(body, status_code=402, headers={"Payment-Needed": encoded})


def _issue_license_key(order, trade_no, amount):
    """签发改写买断授权码，授权码绑定机器码 + 订单号。"""
    if not config.LICENSE_PRIVATE_KEY:
        raise RuntimeError("授权签署私钥缺失（LICENSE_PRIVATE_KEY）")
    payload = {
        "oid": order["out_trade_no"],
        "amt": amount,
        "mid": order.get("machine_code") or "*",
        "iat": datetime.now(timezone.utc).isoformat(),
        "exp": "",
        "mode": "perpetual",
    }
    return crypto_sign.sign_license(config.LICENSE_PRIVATE_KEY, payload)


def _verify_and_deliver(payment_proof: str):
    # 1. 解码支付凭证
    try:
        proof = json.loads(crypto_sign.b64url_decode(payment_proof))
        protocol = proof.get("protocol", {})
        payment_proof_value = protocol.get("payment_proof")
        trade_no = protocol.get("trade_no")
        client_session = proof.get("method", {}).get("client_session")
    except Exception:
        return _issue_402("*")

    if not payment_proof_value or not trade_no:
        return _issue_402("*")

    # 2. 调用支付宝验付
    try:
        resp = verify_payment(payment_proof_value, trade_no, client_session)
    except RuntimeError as e:
        return JSONResponse({"code": "CONFIG_ERROR", "message": str(e)}, status_code=500)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"code": "VERIFY_FAILED", "message": f"支付宝验付异常：{e}"}, status_code=500
        )

    if resp.get("code") != "10000":
        return _issue_402("*")

    returned_trade_no = resp.get("trade_no") or resp.get("tradeNo") or ""
    out_trade_no = resp.get("out_trade_no") or resp.get("outTradeNo") or ""
    returned_amount = resp.get("amount")
    returned_resource_id = resp.get("resource_id") or resp.get("resourceId") or ""
    active = resp.get("active")

    repository = orders
    order = repository.find_by_out_trade_no(out_trade_no) if out_trade_no else None
    sandbox_mode = config.is_sandbox()
    verify_trade_no = returned_trade_no or (trade_no if sandbox_mode else "")
    verify_amount = returned_amount or (
        order.get("amount") if sandbox_mode and order else ""
    )
    resource_id_verified = returned_resource_id or (
        order.get("resource_id") if sandbox_mode and order else ""
    )

    # 3. 严格校验（active / 金额 / 资源归属 / 幂等 / 订单状态）
    amount_matches = bool(order and _amounts_equal(order.get("amount"), verify_amount))
    resource_matches = bool(
        order
        and order.get("resource_id") == resource_id_verified
        and resource_id_verified == config.RESOURCE_PATH
    )
    fulfill_status = order.get("fulfill_status") if order else None
    fulfillment_in_progress = fulfill_status in ("PENDING_CONFIRM", "FULFILLED")
    order_usable = bool(
        order
        and order.get("currency") == "CNY"
        and order.get("order_status") in ("PENDING_PAYMENT", "PAID")
        and fulfill_status in ("UNFULFILLED", "PENDING_CONFIRM", "FULFILLED")
        and (fulfillment_in_progress or _is_future(order.get("pay_before")))
    )
    if (
        active is not True
        or not verify_trade_no
        or verify_trade_no != trade_no
        or not out_trade_no
        or not resource_id_verified
        or not amount_matches
        or not resource_matches
        or not order_usable
    ):
        return _issue_402(order.get("machine_code") if order else "*")

    # 4. 幂等生成授权码
    fulfillment = repository.prepare_fulfillment(
        {
            "out_trade_no": out_trade_no,
            "trade_no": verify_trade_no,
            "expected_amount": _normalize_amount(verify_amount),
            "expected_resource_id": resource_id_verified,
            "create_resource": lambda: _issue_license_key(order, verify_trade_no, verify_amount),
        }
    )
    if (
        not fulfillment
        or fulfillment.get("state") not in ("PENDING_CONFIRM", "FULFILLED")
        or not fulfillment.get("service_result")
    ):
        raise RuntimeError("prepare_fulfillment 未返回已持久化的履约结果")

    license_key = fulfillment["service_result"]
    if fulfillment["state"] == "FULFILLED":
        return _success(verify_trade_no, out_trade_no, resource_id_verified, license_key, True)

    # 5. 履约确认（失败允许用同一 Payment-Proof 重试）
    if not confirm_fulfillment(verify_trade_no):
        return JSONResponse(
            {
                "code": "FULFILLMENT_CONFIRM_FAILED",
                "message": "资源已生成但履约确认失败，请稍后用同一 Payment-Proof 重试",
            },
            status_code=502,
        )
    repository.mark_fulfilled(out_trade_no, verify_trade_no)
    return _success(verify_trade_no, out_trade_no, resource_id_verified, license_key, False)


def _success(trade_no, out_trade_no, resource_id, license_key, already_fulfilled):
    payment_validation = crypto_sign.b64url_encode(
        json.dumps(
            {
                "trade_no": trade_no,
                "out_trade_no": out_trade_no,
                "validated": True,
                "resource_id": resource_id,
            },
            ensure_ascii=False,
        )
    )
    body = {
        "resource_id": resource_id,
        "license_key": license_key,
        "trade_no": trade_no,
        "out_trade_no": out_trade_no,
        "amount": config.PRICE_CNY,
        "perpetual": True,
        "already_fulfilled": already_fulfilled,
        "fulfillment_confirmed": True,
    }
    return JSONResponse(body, headers={"Payment-Validation": payment_validation})


# ----------------------- 路由 -----------------------

@app.post(config.RESOURCE_PATH)
async def license_resource(request: Request):
    payment_proof = request.headers.get("Payment-Proof")
    if payment_proof and payment_proof.strip():
        return _verify_and_deliver(payment_proof.strip())

    machine_code = "*"
    try:
        body = await request.json() or {}
        machine_code = (body.get("machine_code") or "*").strip() or "*"
    except Exception:  # noqa: BLE001
        machine_code = "*"
    return _issue_402(machine_code)


@app.get(config.RESOURCE_PATH)
async def license_resource_get_probe():
    """GET 探测同样出 402（支付宝后台「线上验证」等可能用 GET 探活）。"""
    return _issue_402("*")


@app.get("/api/license/status")
async def license_status(out_trade_no: str = ""):
    if not out_trade_no:
        return JSONResponse({"code": "MISSING_PARAM", "message": "out_trade_no required"}, status_code=400)
    order = orders.find_by_out_trade_no(out_trade_no)
    if not order:
        return JSONResponse({"out_trade_no": out_trade_no, "found": False}, status_code=404)
    return JSONResponse(
        {
            "out_trade_no": out_trade_no,
            "found": True,
            "amount": order["amount"],
            "order_status": order["order_status"],
            "fulfill_status": order["fulfill_status"],
            "trade_no": order["trade_no"],
            "has_license": bool(order["license_key"]),
        }
    )


@app.get("/api/orders")
async def list_orders(request: Request):
    """导出全部订单，供商户云端控制台同步到云数据库。

    安全：① 整站受 sharecode 网关保护（不带 sharecode 直接 403）；
    ② 若设置了 ADMIN_TOKEN，则还需携带 ?token= 或 x-admin-token 头，否则 401。
    不返回授权码原文（license_key），仅给 has_license 标记。
    """
    admin_token = os.environ.get("ADMIN_TOKEN")
    if admin_token:
        provided = request.query_params.get("token") or request.headers.get("x-admin-token", "")
        if provided != admin_token:
            return JSONResponse({"code": "UNAUTHORIZED", "message": "需要有效的 admin token"}, status_code=401)
    try:
        return {"ok": True, "count": len(orders.find_all()), "orders": orders.find_all()}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"code": "EXPORT_FAILED", "message": str(e)}, status_code=500)


@app.get("/health")
async def health():
    return {"ok": True, "sandbox": config.is_sandbox()}


@app.get("/")
async def root_redirect(request: Request):
    """根路径无路由，重定向到商户控制台并保留 sharecode。"""
    sharecode = request.query_params.get("sharecode", "")
    target = "/console/" + (f"?sharecode={sharecode}" if sharecode else "")
    return RedirectResponse(url=target, status_code=307)
