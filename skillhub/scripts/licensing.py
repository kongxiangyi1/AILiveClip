# -*- coding: utf-8 -*-
"""本地授权核心: 机器码 / 签发 / 验签。

★ 计费模式: 支付宝一次性买断。主通道是 402 沙箱服务端(见 payment_gate.py), 服务端验付通过后
  签发一枚授权码; 兜底通道是作者用本地私钥人工签发。**两种通道的授权码格式完全一致**,
  都由本模块在本机验签 —— 因此有授权时 probe 根本不请求服务端, 不产生重复费用。
  (平台不代收: SkillHub 只做展示, 钱直接进开发者账户。)

授权码格式:  LIVECLIP1.<payload_b64url>.<sig_b64url>

payload(JSON):
  {
    "oid":  订单号/交易号,
    "amt":  "19.90",
    "mid":  机器码, "*" 表示不限机器,
    "iat":  签发时间(ISO),
    "exp":  "" 表示永久, 否则 ISO 过期时间,
    "mode": "perpetual"
  }

签名算法:
  - 首选 RSA-SHA256 + PKCS1v15(需 `cryptography`), 公钥随包分发, 私钥只在作者手上
  - 降级 HMAC-SHA256(密钥随包分发, 仅防手改不防破解, 启动时打印告警)
"""
import base64
import hashlib
import hmac
import json
import os
import platform
import subprocess
import sys

SCHEME = "LIVECLIP1"
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(PKG_DIR, "config.json")

DEFAULT_CONFIG = {
    "billing_mode": "local_license",
    "price_cny": "19.90",
    "alipay": {"pay_url": "", "pay_note": ""},
    "license": {"public_key_pem": "", "hmac_secret": ""},
}


# ---------------- 编码 / 配置 ----------------

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_dec(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64u_dec_strict(text: str) -> bytes:
    """严格解码: 拒绝尾部多余位被篡改的变体(不同字符可能解出同一字节)。

    base64 尾字符可能携带不参与解码的位, 宽松解码会让「改一个尾字符」仍然验签通过,
    因此验签前必须确认 重编码(解码结果) == 原串。
    """
    raw = b64u_dec(text)
    if b64u(raw) != text:
        raise ValueError("base64 编码不规范(尾部存在多余位, 疑似被改写)")
    return raw


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
        for k, v in loaded.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------- 机器码 ----------------

def machine_code() -> str:
    """本机标识: 主板序列号 + 主机名 + 用户名, 取 sha256 前 16 位。

    取不到主板序列号时退化为 主机名+用户名+MAC, 保证同一台机器稳定同一值。
    """
    raw = ""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["wmic", "baseboard", "get", "serialnumber"],
                capture_output=True, text=True, timeout=10)
            lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
            if len(lines) >= 2 and lines[1] and "SerialNumber" not in lines[1]:
                raw = lines[1]
    except (OSError, subprocess.SubprocessError):
        raw = ""
    if not raw:
        raw = f"{platform.node()}|{os.environ.get('USERNAME', '')}|{_mac()}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16].upper()


def _mac() -> str:
    import uuid
    return f"{uuid.getnode():012x}"


# ---------------- 签名 / 验签 ----------------

def _has_crypto() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def sign_payload(payload: dict, private_key_pem: str = "", secret: str = "") -> str:
    """作者侧签名, 返回授权码。私钥优先, 无私钥则用 HMAC。"""
    body = b64u(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    if private_key_pem:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None)
        sig = key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
    elif secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    else:
        raise ValueError("缺少私钥或 HMAC 密钥, 无法签发")
    return f"{SCHEME}.{body}.{b64u(sig)}"


def verify_key(key: str, cfg: dict | None = None) -> tuple[bool, dict, str]:
    """验签授权码 -> (是否通过, payload, 原因)。纯本地, 不发网络请求。"""
    cfg = cfg or load_config()
    parts = (key or "").strip().split(".")
    if len(parts) != 3 or parts[0] != SCHEME:
        return False, {}, "授权码格式不正确(应为 LIVECLIP1.xxx.yyy)"
    _, body, sig = parts
    try:
        raw_body = b64u_dec_strict(body)
        raw_sig = b64u_dec_strict(sig)
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as e:
        return False, {}, f"授权码内容无法解析({e})"

    pub = (cfg.get("license") or {}).get("public_key_pem", "")
    secret = (cfg.get("license") or {}).get("hmac_secret", "")
    if pub:
        if not _has_crypto():
            return False, payload, "本包使用 RSA 授权, 需先安装: pip install cryptography"
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.exceptions import InvalidSignature
        try:
            k = serialization.load_pem_public_key(pub.encode())
            k.verify(raw_sig, body.encode(), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            return False, payload, "授权码签名校验失败(与包内公钥不匹配)"
        except (ValueError, TypeError) as e:
            return False, payload, f"公钥无效: {e}"
    elif secret:
        expect = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expect, raw_sig):
            return False, payload, "授权码校验失败"
    else:
        return False, payload, "包内未配置公钥/密钥, 无法校验授权码"

    mid = payload.get("mid", "*")
    if mid not in ("*", "", machine_code()):
        return False, payload, f"授权码绑定了其他机器(本机机器码 {machine_code()})"

    exp = payload.get("exp", "")
    if exp:
        import datetime
        try:
            if datetime.datetime.now() >= datetime.datetime.fromisoformat(exp):
                return False, payload, f"授权码已于 {exp} 过期"
        except ValueError:
            return False, payload, f"授权码过期时间格式错误: {exp}"

    amt = str(payload.get("amt", ""))
    price = str(cfg.get("price_cny", ""))
    if price and amt and abs(_f(amt) - _f(price)) > 1e-6:
        print(f"[警告] 授权码金额 {amt} 与包内约定价 {price} 不一致")
    return True, payload, "OK"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def is_perpetual(payload: dict) -> bool:
    return not payload.get("exp") and payload.get("mode", "perpetual") == "perpetual"


if __name__ == "__main__":
    print("machine_code =", machine_code())
    print("config =", CONFIG_FILE)
    print("RSA 可用 =", _has_crypto())
    sys.exit(0)
