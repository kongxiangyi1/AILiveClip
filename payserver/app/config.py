"""服务端配置加载。

只从环境变量 / .env 读取，不硬编码任何密钥；本机沙箱配置缺失时仍允许服务启动，
但出账与验付接口会按需报错（不伪装成功）。生产环境必须将 ALIPAY_GATEWAY 显式设为
https://openapi.alipay.com/gateway.do，且 SERVICE_ID 替换为服务市场真实 serviceId。
"""
import os

from dotenv import load_dotenv

load_dotenv()

RESOURCE_PATH = os.environ.get("AI_CLIP_RESOURCE_PATH", "/api/license")
PRICE_CNY = os.environ.get("AI_CLIP_PRICE_CNY", "19.90")
GOODS_NAME = os.environ.get("AI_CLIP_GOODS_NAME", "直播切片工具·买断授权")
SELLER_NAME = os.environ.get("ALIPAY_SELLER_NAME", "直播切片工具")
# 沙箱固定网关；生产部署时显式覆盖为 https://openapi.alipay.com/gateway.do
GATEWAY = os.environ.get(
    "ALIPAY_GATEWAY", "https://openapi-sandbox.dl.alipaydev.com/gateway.do"
)
# 沙箱固定 service_id；生产替换为服务市场真实 serviceId，且禁止向用户索要正式 serviceId 做联调
SERVICE_ID = os.environ.get("ALIPAY_SERVICE_ID", "api_mock_service_id")


def _read_key(path_key: str, file_key: str) -> str:
    """密钥按优先级读取：环境变量原值 > 文件原始内容（去掉首尾空白）。

    文件应存放「无 PEM 头尾的原始 Base64（PKCS#1）」字符串，与 alipay-sdk-python
    config.app_private_key 的约定一致，禁止写入 PEM 头尾或做格式转换。
    """
    raw = os.environ.get(path_key)
    if raw:
        return raw.strip()
    fp = os.environ.get(file_key)
    if fp and os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            return f.read().strip()
    return ""


APP_ID = os.environ.get("ALIPAY_APP_ID", "")
SELLER_ID = os.environ.get("ALIPAY_SELLER_ID", "")
APP_PRIVATE_KEY = _read_key("ALIPAY_APP_PRIVATE_KEY", "ALIPAY_APP_PRIVATE_KEY_FILE")
ALIPAY_PUBLIC_KEY = _read_key("ALIPAY_ALIPAY_PUBLIC_KEY", "ALIPAY_ALIPAY_PUBLIC_KEY_FILE")
# 签发改写买断授权码（LIVECLIP1.*）的 RSA 私钥，须与 skill 包内 config.json 的 public_key_pem 成对
LICENSE_PRIVATE_KEY = _read_key("LICENSE_PRIVATE_KEY", "LICENSE_PRIVATE_KEY_FILE")

DB_PATH = os.environ.get(
    "ORDER_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "orders.db"),
)


def is_sandbox() -> bool:
    return GATEWAY.startswith("https://openapi-sandbox") and SERVICE_ID == "api_mock_service_id"
