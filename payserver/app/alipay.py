"""支付宝 AI 按量付费 SDK 封装：预下单 + 支付凭证验付 + 履约确认。

官方三步：alipay.aipay.agent.payment.precreate -> .verify ->
alipay.aipay.agent.fulfillment.confirm。
配置缺失时显式报错，不降级到 Mock / 固定成功。
Python SDK 必须使用 Model 类（biz_model），不能直接用 dict。
"""
import json

from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
from alipay.aop.api.domain.AlipayAipayAgentFulfillmentConfirmModel import (
    AlipayAipayAgentFulfillmentConfirmModel,
)
from alipay.aop.api.domain.AlipayAipayAgentPaymentPrecreateModel import (
    AlipayAipayAgentPaymentPrecreateModel,
)
from alipay.aop.api.domain.AlipayAipayAgentPaymentVerifyModel import (
    AlipayAipayAgentPaymentVerifyModel,
)
from alipay.aop.api.request.AlipayAipayAgentFulfillmentConfirmRequest import (
    AlipayAipayAgentFulfillmentConfirmRequest,
)
from alipay.aop.api.request.AlipayAipayAgentPaymentPrecreateRequest import (
    AlipayAipayAgentPaymentPrecreateRequest,
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


def precreate_payment(out_trade_no: str, amount: str, goods_name: str,
                      pay_before: str, currency: str = "CNY") -> str:
    """按量付费预下单，返回 amt_pre_pay_id（官方三步的第 0 步）。

    文档: alipay.aipay.agent.payment.precreate
    必填: out_trade_no / service_id / goods_name / amount / pay_before / currency
    注意: 参数里没有 seller_id —— 收款方由 app_id + 应用私钥签名决定，
    所以这一步不依赖 .env 的 ALIPAY_SELLER_ID。

    pay_before 须为无微秒的 ISO8601（官方文档 string[1,32]，
    带 6 位微秒会刚好顶到 32 字符边界，易被拒）。
    """
    req = AlipayAipayAgentPaymentPrecreateRequest()
    model = AlipayAipayAgentPaymentPrecreateModel()
    model.out_trade_no = out_trade_no
    model.service_id = config.SERVICE_ID
    model.goods_name = goods_name
    model.amount = str(amount)
    model.pay_before = pay_before
    model.currency = currency
    req.biz_model = model
    resp = json.loads(client().execute(req))
    data = resp.get("alipay_aipay_agent_payment_precreate_response", resp)
    pre_pay_id = data.get("amt_pre_pay_id") or data.get("amtPrePayId") or ""
    if data.get("code") != "10000" or not pre_pay_id:
        raise RuntimeError(
            "预下单失败: code={} msg={} sub_code={} sub_msg={}".format(
                data.get("code"), data.get("msg"),
                data.get("sub_code"), data.get("sub_msg"),
            )
        )
    return pre_pay_id


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
