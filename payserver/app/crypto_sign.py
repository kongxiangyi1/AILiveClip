"""本地 RSA 签名工具（买断授权码 / 商家账单签名）。

- 商家账单 seller_signature：按合约用 key 字典序拼接固定 8 字段后 PKCS#1v15 + SHA256 签名。
- 买断授权码：严格沿用 ai-live-clip skill 包内 licensing.py 的格式 LIVECLIP1.<payload>.<sig>，
  服务端签名、客户端用 config.json 公钥验签。
私钥一律以「无 PEM 头尾的原始 Base64（PKCS#1）」传入，禁止写入 PEM 头尾或做格式转换。
"""
import base64
import hashlib
import json

from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15


def _b64(raw: str) -> str:
    return raw.strip()


def _load_rsa_private_key(raw: str):
    """解析 RSA 私钥，兼容两种传入形式：
    ① 无 PEM 头尾的原始 Base64 DER（PKCS#1 / PKCS#8）；
    ② 标准 PEM（含 -----BEGIN ... ----- 头尾）。
    统一返回 RSA 私钥对象。"""
    raw = raw.strip()
    if "-----BEGIN" in raw:
        return RSA.import_key(raw)
    return RSA.import_key(base64.b64decode(_b64(raw), validate=True))


def sign_pkcs1v15_b64(raw_private_key_b64: str, sign_string: str) -> str:
    """用 RSA 私钥对字符串做 SHA256 + PKCS#1v15 签名，返回 Base64。
    私钥支持「裸 Base64 DER」或「PEM」两种形式。"""
    key = _load_rsa_private_key(raw_private_key_b64)
    digest = SHA256.new(sign_string.encode("utf-8"))
    signature = pkcs1_15.new(key).sign(digest)
    return base64.b64encode(signature).decode("utf-8")


def seller_sign(params: dict, raw_private_key_b64: str) -> str:
    """商家账单签名：仅签 amount/currency/goods_name/out_trade_no/pay_before/resource_id/
    seller_id/service_id 八个字段，key 字典序拼接，跳过空值。"""
    keys = sorted(params.keys())
    parts = []
    for k in keys:
        v = params[k]
        if v is not None and v != "":
            parts.append(f"{k}={v}")
    sign_string = "&".join(parts)
    return sign_pkcs1v15_b64(raw_private_key_b64, sign_string)


def b64url_encode(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def b64url_decode(data: str) -> str:
    pad = 4 - len(data) % 4
    if pad != 4:
        data += "=" * pad
    return base64.urlsafe_b64decode(data).decode("utf-8")


def sign_license(raw_private_key_b64: str, payload: dict) -> str:
    """签发买断授权码 LIVECLIP1.<payload_b64url>.<sig_b64url>。

    payload 字段对齐客户端 licensing.verify_key：oid/amt/mid/iat/exp/mode。
    """
    body = b64url_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    sig = sign_pkcs1v15_b64(raw_private_key_b64, body)
    return f"LIVECLIP1.{body}.{sig}"
