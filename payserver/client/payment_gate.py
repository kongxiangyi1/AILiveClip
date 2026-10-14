# -*- coding: utf-8 -*-
"""客户端付费闸口(零第三方依赖, 纯 stdlib)。

⚠️ 本文件目前放在 payserver/client/, **不随免费版技能包分发**。
   切换为付费技能时才拷入技能包的 scripts/ 并重打包(见 docs/payskill-onboarding.md 第 8 步)。

职责边界(按 SkillHub/支付宝官方要求):
  本脚本只做**编排**, 不自行实现付款、不接触密钥、不自行签名。
  真实支付交给买家侧官方 CLI `alipay-bot`(由 `npx -y @alipay/agent-payment@latest install` 安装)。

子命令(对齐官方 probe/pay/complete 三段式):
  probe    --video <路径>                请求资源接口; 未付款 -> 402, 落 bill 状态文件
  pay      [--session-id ID]             把账单交给 alipay-bot 402-buyer-pay, 展示支付入口等用户付款
  complete [--out-trade-no NO]           付款后查原订单并取得资源(不重新下单)
  complete --proof <B64>                 直连重试(仅 MOCK/离线联调用, 绕过 alipay-bot)
  status   --out-trade-no NO             查询订单状态(排障用)

环境变量:
  AI_CLIP_BILLING_URL      计费服务地址(默认 http://127.0.0.1:8787)
  AI_LIVE_CLIP_WORKDIR     工作区根目录(.pay_cache 落这里; 不传则用 cwd)
  ALIPAY_BOT_CMD           alipay-bot 可执行名(默认 alipay-bot)
  ALIPAY_BOT_BODY_FLAG     传给 402-buyer-pay 的原始请求体参数名(默认 --body)
  ALIPAY_BOT_HEADERS_FLAG  原始请求头参数名(默认 --headers, 置空则不下传)

CLI 参数名提示: 402-buyer-pay / 402-query-payment-status 的 --session-id / --file /
--resource-url / --method / --intent-summary / --out-shake-no 取自官方教程示例; --body /
--headers 官方只说明"POST 还须传入原始请求体和必要请求头"而未给出参数名, 上线前请按你本机
安装的 alipay-payment-skill 文档核对, 不一致时用上面的环境变量覆盖。
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BILLING_URL = os.environ.get("AI_CLIP_BILLING_URL", "http://127.0.0.1:8787").rstrip("/")
RESOURCE_URL = os.environ.get("AI_CLIP_RESOURCE_URL", BILLING_URL + "/api/v1/resource")
WORKDIR = os.environ.get("AI_LIVE_CLIP_WORKDIR") or os.getcwd()
STATE_DIR = os.path.join(WORKDIR, ".pay_cache")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
BILL_FILE = os.path.join(STATE_DIR, "payment-needed")

BOT = os.environ.get("ALIPAY_BOT_CMD", "alipay-bot")
BODY_FLAG = os.environ.get("ALIPAY_BOT_BODY_FLAG", "--body")
HEADERS_FLAG = os.environ.get("ALIPAY_BOT_HEADERS_FLAG", "--headers")


# ---------------- 基础工具 ----------------

def resource_id_for(video_path: str) -> str:
    """按视频路径+体积生成稳定 resource_id(同一文件重复调用得到同一资源标识)。"""
    p = os.path.normpath(os.path.abspath(video_path))
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    h = hashlib.sha1(f"{p.lower()}:{size}".encode("utf-8", "replace")).hexdigest()[:16]
    return f"liveclip_{h}"


def _write_private(path: str, text: str):
    """写入并尽量收紧权限(账单含订单号, 不宜被同机其他用户读取)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)  # Windows 上为 no-op, 尽力而为
    except OSError:
        pass


def save_state(payload: dict):
    _write_private(STATE_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def http_json(method: str, path: str, body: dict | None = None,
              headers: dict | None = None):
    """返回 (status, headers, json_body)。402 等非 2xx 也正常返回, 不抛异常。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BILLING_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body_json = json.loads(raw)
        except ValueError:
            body_json = {"raw": raw}
        return e.code, dict(e.headers), body_json


def hget(headers: dict, name: str):
    """大小写不敏感取响应头(h11 在线上会把头名转小写)。"""
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def decode_payment_needed(header_val: str) -> dict:
    pad = "=" * (-len(header_val) % 4)
    return json.loads(base64.urlsafe_b64decode(header_val + pad))


def run_bot(args: list[str]) -> int:
    """原样执行官方 CLI 并透传输出; 不解析、不改写结果。"""
    print(f"[执行] {BOT} {' '.join(args)}", flush=True)
    proc = subprocess.run([BOT, *args], check=False)
    return proc.returncode


def _authorized(body: dict) -> int:
    print(f"[已授权] 订单 {body.get('out_trade_no')}  金额 {body.get('amount')} CNY")
    print(f"license_token = {body.get('token')}")
    print("可以继续执行本地流水线: asr_pipeline -> segmenter -> cut_pipeline")
    return 0


# ---------------- 子命令 ----------------

def cmd_probe(args):
    rid = resource_id_for(args.video)
    status, headers, body = http_json("POST", "/api/v1/resource", {"resource_id": rid})
    if status == 200:
        return _authorized(body)
    if status == 402:
        pn = hget(headers, "Payment-Needed")
        if not pn:
            print(f"[异常] 返回 402 但缺少 Payment-Needed 响应头: {body}")
            return 402
        bill = decode_payment_needed(pn)
        proto = bill["protocol"]
        _write_private(BILL_FILE, pn)
        save_state({
            "resource_id": rid,
            "resource_url": RESOURCE_URL,
            "method": "POST",
            "body": {"resource_id": rid},
            "out_trade_no": proto["out_trade_no"],
            "amount": proto["amount"],
            "currency": proto["currency"],
            "pay_before": proto["pay_before"],
            "goods_name": bill["method"].get("goods_name", ""),
            "session_id": args.session_id or proto["out_trade_no"],
        })
        print("[402 需支付] 账单已保存, 下一步执行 pay:")
        print(f"  金额 {proto['amount']} {proto['currency']}  订单号 {proto['out_trade_no']}")
        print(f"  支付截止 {proto['pay_before']}")
        print(f"  python payment_gate.py pay")
        return 402
    print(f"[失败] HTTP {status}: {body}")
    return status


def cmd_pay(args):
    state = load_state()
    if not state.get("out_trade_no"):
        print("[错误] 没有待支付账单, 请先执行 probe --video <路径>")
        return 2
    if not os.path.exists(BILL_FILE):
        print(f"[错误] 账单文件缺失: {BILL_FILE}")
        return 2
    cmd = [
        "402-buyer-pay",
        "--session-id", args.session_id or state["session_id"],
        "--file", BILL_FILE,
        "--resource-url", state["resource_url"],
        "--method", state["method"],
        "--intent-summary", f"原始请求：{state['goods_name'] or '直播切片'} "
                           f"(订单 {state['out_trade_no']}, {state['amount']} 元)",
    ]
    # POST 还须带上原始请求体; 官方未固定参数名, 可用环境变量覆盖
    if BODY_FLAG:
        cmd += [BODY_FLAG, json.dumps(state["body"], ensure_ascii=False)]
    if HEADERS_FLAG:
        cmd += [HEADERS_FLAG, "Content-Type: application/json"]
    rc = run_bot(cmd)
    if rc == 0:
        print(f"\n支付完成后执行: python payment_gate.py complete "
              f"--out-trade-no {state['out_trade_no']}")
    return rc


def cmd_complete(args):
    state = load_state()
    out_no = args.out_trade_no or state.get("out_trade_no")
    resource_url = state.get("resource_url", RESOURCE_URL)
    method = state.get("method", "POST")

    # 离线/MOCK 联调分支: 直接带凭证重试, 不经过 alipay-bot
    if args.proof:
        body = {"resource_id": state.get("resource_id", "")}
        if out_no:
            body["out_trade_no"] = out_no
        status, _, resp = http_json("POST", "/api/v1/resource", body,
                                    headers={"Payment-Proof": args.proof})
        if status == 200:
            return _authorized(resp)
        print(f"[未放行] HTTP {status}: {resp}")
        return status

    if not out_no:
        print("[错误] 需要 --out-trade-no, 或先执行 probe 生成状态")
        return 2
    # 官方流程: 查原订单 + 复用原请求取得资源; 成功路径不追加独立回执
    return run_bot([
        "402-query-payment-status",
        "--out-shake-no", out_no,
        "--resource-url", resource_url,
        "--method", method,
    ])


def cmd_status(args):
    status, _, body = http_json("GET", f"/api/v1/orders/{args.out_trade_no}")
    print(f"HTTP {status}: {json.dumps(body, ensure_ascii=False)}")
    return 0 if status == 200 else status


def main():
    ap = argparse.ArgumentParser(description="ai-live-clip 付费闸口(官方 alipay-bot 编排)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="请求资源接口, 未付款返回 402 并保存账单")
    p.add_argument("--video", required=True, help="原视频路径(用于生成 resource_id)")
    p.add_argument("--session-id", help="会话 ID(默认用订单号)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("pay", help="把账单交给官方 alipay-bot, 展示支付入口等待付款")
    p.add_argument("--session-id", help="会话 ID(默认用 probe 时保存的值)")
    p.set_defaults(func=cmd_pay)

    p = sub.add_parser("complete", help="付款后查原订单并取得资源(不重新下单)")
    p.add_argument("--out-trade-no", help="402 账单里的商户订单号")
    p.add_argument("--proof", help="[仅 MOCK/离线联调] 直接带 Payment-Proof 重试")
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("status", help="查询订单状态(排障用)")
    p.add_argument("--out-trade-no", required=True)
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
