# -*- coding: utf-8 -*-
"""支付宝 A2M(AI按量付费) 对接层。

对齐官方 alipay-aipay Skill v1.7.0 的 Python 契约
(references/integration/modules/alipay-sdk-reminder.md +
 modules/interface-contract/aipay-interface-contract.md +
 modules/code-examples/python/4-按量付费/A2MPaymentDemo.md):

  - Python 属**非 Java**, 应用私钥必须用 **PKCS#1 原始 Base64**(`appPrivatePkcsKey`);
    禁转 PKCS#8、禁加 PEM 头尾、禁 base64 二次编解码
  - 开放接口必须用 `alipay-sdk-python` + **Model 类**(不能用 dict 赋 biz_model)
  - 沙箱网关固定 `https://openapi-sandbox.dl.alipaydev.com/gateway.do`;
    只有生产部署才显式把 ALIPAY_GATEWAY 设为生产网关
  - SDK execute() 返回可能是嵌套或扁平结构, 两种都要兼容

MOCK 模式(ALIPAY_MOCK=1): 仅用于本地/沙箱联调, **不是支付校验旁路**。
设计约束(对齐官方 checklist「支付校验无旁路」「沙箱与生产共用相同验证控制流」):
  1. MOCK 只替换"网络往返 + 密钥来源", 订单匹配 / 资源防串 / 金额一致性 /
     幂等履约 / pay_before 过期 等全部业务校验与生产走**同一份代码**, 无分支;
  2. MOCK 仅在"沙箱网关 + api_mock_service_id"下可启用, 生产网关下直接拒绝启动;
  3. 非 MOCK 时配置缺失或仍是占位 serviceId 一律硬失败, 阻止真实 SDK 调用。
"""
import base64
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# ---- 环境配置(见 config.env.example) ----
APP_ID = os.environ.get("ALIPAY_APP_ID", "")
# PKCS#1 原始 Base64(无 PEM 头尾、无换行); 沙箱取 appPrivatePkcsKey
APP_PRIVATE_KEY = os.environ.get("ALIPAY_APP_PRIVATE_KEY", "")
ALIPAY_PUBLIC_KEY = os.environ.get("ALIPAY_PUBLIC_KEY", "")
SELLER_ID = os.environ.get("ALIPAY_SELLER_ID", "")
SELLER_NAME = os.environ.get("ALIPAY_SELLER_NAME", "")
SERVICE_ID = os.environ.get("ALIPAY_SERVICE_ID", "api_mock_service_id")
# 默认沙箱网关(与官方示例一致); 生产部署时显式设为生产网关
GATEWAY = os.environ.get(
    "ALIPAY_GATEWAY", "https://openapi-sandbox.dl.alipaydev.com/gateway.do")
MOCK = os.environ.get("ALIPAY_MOCK", "1") == "1"

SANDBOX_GATEWAY = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"
PRODUCTION_GATEWAY = "https://openapi.alipay.com/gateway.do"
SANDBOX_SERVICE_ID = "api_mock_service_id"

# 生产必需的敏感配置; 缺失即硬失败(禁止占位值继续跑真实 SDK)
REQUIRED_PROD_CONFIG = ("ALIPAY_APP_ID", "ALIPAY_APP_PRIVATE_KEY",
                        "ALIPAY_PUBLIC_KEY", "ALIPAY_SELLER_ID",
                        "ALIPAY_SERVICE_ID")

# 账单签名字段(按 key 字典序拼接)
SIGN_FIELDS = ["amount", "currency", "goods_name", "out_trade_no",
               "pay_before", "resource_id", "seller_id", "service_id"]


# ---------------- 通用工具 ----------------

def normalize_amount(value):
    """金额归一化为两位小数字符串; 非法返回 None。"""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
        return None
    return f"{Decimal(text):.2f}"


def amounts_equal(left, right):
    a, b = normalize_amount(left), normalize_amount(right)
    return a is not None and b is not None and a == b


def is_future(value):
    """pay_before 是否仍未过期(防旧账单重放)。"""
    try:
        return datetime.fromisoformat(str(value)).timestamp() > time.time()
    except (TypeError, ValueError):
        return False


def is_exact_sandbox_mode():
    return GATEWAY == SANDBOX_GATEWAY and SERVICE_ID == SANDBOX_SERVICE_ID


def mock_enabled() -> bool:
    """MOCK 是否真正生效: 必须显式开启且指向沙箱。生产网关下恒为 False。"""
    return MOCK and is_exact_sandbox_mode()


def assert_runtime_config():
    """启动期硬校验, 失败即拒绝服务(官方 checklist: 配置无效必须明确失败)。

    - 生产网关 + MOCK=1  -> 拒绝启动(防支付校验旁路进入生产)
    - 非 MOCK 且缺生产配置 -> 拒绝启动
    - 非 MOCK 且仍为 api_mock_service_id -> 拒绝启动(上线前必须换真实 serviceId)
    """
    if MOCK and not is_exact_sandbox_mode():
        raise RuntimeError(
            f"拒绝启动: ALIPAY_MOCK=1 仅允许在沙箱网关({SANDBOX_GATEWAY}) + "
            f"{SANDBOX_SERVICE_ID} 下使用; 当前 GATEWAY={GATEWAY}, SERVICE_ID={SERVICE_ID}")
    if not MOCK:
        if GATEWAY == PRODUCTION_GATEWAY and SERVICE_ID == SANDBOX_SERVICE_ID:
            raise RuntimeError(
                "拒绝启动: 生产网关仍使用 api_mock_service_id, "
                "上线前须替换为服务市场注册的真实 serviceId")
        missing = [k for k in REQUIRED_PROD_CONFIG if not os.environ.get(k, "").strip()]
        if missing:
            raise RuntimeError(
                f"拒绝启动: 缺少生产配置 {', '.join(missing)}; "
                "私钥只从环境变量/密钥管理服务注入, 禁止写入代码或日志")


def base64url_encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def base64url_decode(data):
    data += "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data).decode("utf-8")


def proof_hash(proof_b64: str) -> str:
    return hashlib.sha256(proof_b64.encode()).hexdigest()[:32]


# ---------------- 商家账单签名(本地完成, 不请求支付宝) ----------------

_EPHEMERAL_KEY = None


def _signing_key():
    """取签名密钥: 生产用配置的 PKCS#1 私钥; 沙箱 MOCK 用一次性内存密钥。

    MOCK 不跳过签名, 只是换一把**临时生成、不落盘、不入配置**的密钥,
    因此签名代码路径与生产完全一致, 不存在"假签名"旁路。
    """
    global _EPHEMERAL_KEY
    if APP_PRIVATE_KEY:
        from Crypto.PublicKey import RSA
        return RSA.import_key(base64.b64decode(APP_PRIVATE_KEY, validate=True))
    if mock_enabled():
        if _EPHEMERAL_KEY is None:
            from Crypto.PublicKey import RSA
            _EPHEMERAL_KEY = RSA.generate(2048)
        return _EPHEMERAL_KEY
    raise RuntimeError("未配置 ALIPAY_APP_PRIVATE_KEY(PKCS#1 原始 Base64)")


def rsa2_sign(content: str) -> str:
    """RSA2(SHA256withRSA) 签名, base64 输出。

    Python 使用 PKCS#1 裸 Base64 私钥。按 SDK 提醒的「调用边界」, 只在调用原生
    密码库时临时把裸 Base64 解成 DER 密钥对象, 不写回配置、不修改原始值。
    """
    from Crypto.Hash import SHA256
    from Crypto.Signature import pkcs1_15
    sig = pkcs1_15.new(_signing_key()).sign(SHA256.new(content.encode("utf-8")))
    return base64.b64encode(sig).decode("utf-8")


def build_bill(resource_id: str, amount: str, goods_name: str,
               pay_ttl_minutes: int = 30):
    """生成 402 账单(protocol + method 两层), 返回 (bill, out_trade_no)。"""
    amount = normalize_amount(amount) or amount
    out_trade_no = f"LC_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    pay_before = (datetime.now(timezone.utc).astimezone()
                  + timedelta(minutes=pay_ttl_minutes)).isoformat()
    sign_content = "&".join(
        f"{k}={v}" for k, v in sorted([
            ("amount", amount), ("currency", "CNY"), ("goods_name", goods_name),
            ("out_trade_no", out_trade_no), ("pay_before", pay_before),
            ("resource_id", resource_id), ("seller_id", SELLER_ID),
            ("service_id", SERVICE_ID)]))
    bill = {
        "protocol": {
            "out_trade_no": out_trade_no,
            "amount": amount,
            "currency": "CNY",
            "resource_id": resource_id,
            "pay_before": pay_before,
            "seller_signature": rsa2_sign(sign_content),
            "seller_sign_type": "RSA2",
            "seller_unique_id": SELLER_ID,
        },
        "method": {
            "seller_name": SELLER_NAME,
            "seller_id": SELLER_ID,
            "seller_app_id": APP_ID,
            "goods_name": goods_name,
            "seller_unique_id_key": "seller_id",
            "service_id": SERVICE_ID,
        },
    }
    return bill, out_trade_no


def encode_payment_needed(bill: dict) -> str:
    """Payment-Needed Header: Base64URL(账单JSON)。"""
    return base64url_encode(json.dumps(bill, ensure_ascii=False))


def encode_payment_validation(trade_no: str, out_trade_no: str,
                              resource_id: str) -> str:
    """Payment-Validation Header: 交付成功的回执, 官方联调成功证据之一。

    格式对齐官方 Python 示例 successful_resource_response():
    Base64URL({trade_no, out_trade_no, validated: true, resource_id})
    """
    return base64url_encode(json.dumps({
        "trade_no": trade_no, "out_trade_no": out_trade_no,
        "validated": True, "resource_id": resource_id,
    }, ensure_ascii=False))


def decode_proof(proof_b64: str) -> dict:
    """解析 Payment-Proof: {protocol:{payment_proof,trade_no}, method:{client_session}}。"""
    return json.loads(base64url_decode(proof_b64))


# ---------------- 支付宝开放接口(SDK + Model 类) ----------------

def _client():
    from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
    from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
    cfg = AlipayClientConfig()
    cfg.server_url = GATEWAY
    cfg.app_id = APP_ID
    cfg.app_private_key = APP_PRIVATE_KEY
    cfg.alipay_public_key = ALIPAY_PUBLIC_KEY
    cfg.charset = "utf-8"
    cfg.sign_type = "RSA2"
    return DefaultAlipayClient(alipay_client_config=cfg)


def _unwrap(raw: str, response_key: str) -> dict:
    """SDK execute() 返回可能是嵌套或扁平结构, 两种都要兼容。"""
    data = json.loads(raw)
    return data.get(response_key, data)


def _sdk_verify(p_proof, trade_no, client_session,
                expect_out_trade_no, expect_resource_id, expect_amount) -> dict:
    """验付的**网络层**。MOCK 只替换这一层, 返回值结构与真实响应一致。"""
    if mock_enabled():
        # 沙箱凭证约定: trade_no = MOCK_<out_trade_no>
        if not trade_no or not trade_no.startswith("MOCK_"):
            return {"code": "40002", "sub_msg": "MOCK 凭证格式非法"}
        return {"code": "10000", "active": True, "trade_no": trade_no,
                "out_trade_no": trade_no[len("MOCK_"):],
                "resource_id": expect_resource_id or "",
                "amount": expect_amount, "mock": True}

    from alipay.aop.api.domain.AlipayAipayAgentPaymentVerifyModel import (
        AlipayAipayAgentPaymentVerifyModel)
    from alipay.aop.api.request.AlipayAipayAgentPaymentVerifyRequest import (
        AlipayAipayAgentPaymentVerifyRequest)
    model = AlipayAipayAgentPaymentVerifyModel()
    model.payment_proof = p_proof
    model.trade_no = trade_no
    if client_session:
        model.client_session = client_session
    req = AlipayAipayAgentPaymentVerifyRequest()
    req.biz_model = model
    return _unwrap(_client().execute(req),
                   "alipay_aipay_agent_payment_verify_response")


def verify_proof(proof_b64: str, expect_out_trade_no=None,
                 expect_resource_id: str = "", expect_amount=None) -> dict:
    """验付。接口成功只是第一步, 业务字段必须逐项核对。

    MOCK 与生产共用下方同一份业务校验(无 if-mock 分支), 只替换网络层。
    """
    proof = decode_proof(proof_b64)
    proto = proof.get("protocol", {})
    p_proof = proto.get("payment_proof")
    trade_no = proto.get("trade_no")
    client_session = proof.get("method", {}).get("client_session") or None

    if not (p_proof and trade_no):
        return {"ok": False, "reason": "Payment-Proof 缺 payment_proof/trade_no"}

    body = _sdk_verify(p_proof, trade_no, client_session,
                       expect_out_trade_no, expect_resource_id, expect_amount)
    if body.get("code") != "10000":
        return {"ok": False, "reason": body.get("sub_msg") or "验付接口未成功"}

    out_no = body.get("out_trade_no") or body.get("outTradeNo") or ""
    res_id = body.get("resource_id") or body.get("resourceId") or ""
    amount = body.get("amount")
    ret_trade_no = body.get("trade_no") or body.get("tradeNo") or trade_no

    ok = (body.get("active") is True
          and bool(ret_trade_no)
          and ret_trade_no == trade_no
          and bool(out_no)
          and bool(res_id))
    # 有期望值时严格比对(防改价、防串用、防张冠李戴)
    if ok and expect_amount is not None:
        ok = amounts_equal(amount, expect_amount)
    if ok and expect_resource_id:
        ok = res_id == expect_resource_id
    if ok and expect_out_trade_no:
        ok = out_no == expect_out_trade_no
    # 沙箱下支付宝可能不回传这些字段, 以本地订单为准
    return {"ok": ok, "trade_no": ret_trade_no, "payment_proof": p_proof,
            "out_trade_no": out_no or expect_out_trade_no,
            "resource_id": res_id or expect_resource_id,
            "amount": amount if amount is not None else expect_amount,
            "active": body.get("active"), "sandbox": is_exact_sandbox_mode()}


def fulfillment_confirm(trade_no: str) -> dict:
    """履约确认: 业务入参只有 trade_no。确认成功后才允许标记订单最终完成。

    MOCK 只替换网络层; 调用方对返回值的处理与生产完全一致(失败即不交付、可重试)。
    """
    if mock_enabled():
        return {"ok": True, "trade_no": trade_no, "mock": True}
    from alipay.aop.api.domain.AlipayAipayAgentFulfillmentConfirmModel import (
        AlipayAipayAgentFulfillmentConfirmModel)
    from alipay.aop.api.request.AlipayAipayAgentFulfillmentConfirmRequest import (
        AlipayAipayAgentFulfillmentConfirmRequest)
    model = AlipayAipayAgentFulfillmentConfirmModel()
    model.trade_no = trade_no
    req = AlipayAipayAgentFulfillmentConfirmRequest()
    req.biz_model = model
    body = _unwrap(_client().execute(req),
                   "alipay_aipay_agent_fulfillment_confirm_response")
    return {"ok": body.get("code") == "10000", "trade_no": trade_no}
