"""支付宝 AI 按量付费 SDK 封装：支付凭证验付 + 履约确认。

只调用 alipay.aipay.agent.payment.verify 与 alipay.aipay.agent.fulfillment.confirm。
配置缺失时显式报错，不降级到 Mock / 固定成功。
Python SDK 必须使用 Model 类（biz_model），不能直接用 dict。
"""
import json

from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
from alipay.aop.api.domain.AlipayAipayAgentFulfillmentConfirmModel import (
    AlipayAipayAgentFulfillmentConfirmModel,
)
from alipay.aop.api.domain.AlipayAipayAgentPaymentVerifyModel import (
    AlipayAipayAgentPaymentVerifyModel,
)
from alipay.aop.api.request.AlipayAipayAgentFulfillmentConfirmRequest import (
    AlipayAipayAgentFulfillmentConfirmRequest,
)
from alipay.aop.api.request.AlipayAipayAgentPaymentVerifyRequest import (
    AlipayAipayAgentPaymentVerifyRequest,
)

from . import config

_client = None


def client():
    global _client
    if _client is None:
        if not (config.APP_ID and config.APP_PRIVATE_KEY and config.ALIPAY_PUBLIC_KEY):
            raise RuntimeError(
                "支付宝配置缺失：请设置 ALIPAY_APP_ID / ALIPAY_APP_PRIVATE_KEY / "
                "ALIPAY_ALIPAY_PUBLIC_KEY（沙箱未配置）"
            )
        cfg = AlipayClientConfig()
        cfg.server_url = config.GATEWAY
        cfg.app_id = config.APP_ID
        cfg.app_private_key = config.APP_PRIVATE_KEY
        cfg.alipay_public_key = config.ALIPAY_PUBLIC_KEY
        cfg.charset = "utf-8"
        cfg.sign_type = "RSA2"
        _client = DefaultAlipayClient(alipay_client_config=cfg)
    return _client


def verify_payment(payment_proof: str, trade_no: str, client_session: str | None = None):
    req = AlipayAipayAgentPaymentVerifyRequest()
    model = AlipayAipayAgentPaymentVerifyModel()
    model.payment_proof = payment_proof
    model.trade_no = trade_no
    if client_session:
        model.client_session = client_session
    req.biz_model = model
    resp = json.loads(client().execute(req))
    return resp.get("alipay_aipay_agent_payment_verify_response", resp)


def confirm_fulfillment(trade_no: str) -> bool:
    req = AlipayAipayAgentFulfillmentConfirmRequest()
    model = AlipayAipayAgentFulfillmentConfirmModel()
    model.trade_no = trade_no
    req.biz_model = model
    resp = json.loads(client().execute(req))
    data = resp.get("alipay_aipay_agent_fulfillment_confirm_response", resp)
    return data.get("code") == "10000"
