# -*- coding: utf-8 -*-
"""客户端付费闸口(零第三方依赖, 纯 stdlib)。

★ 计费模式: **买断(PERPETUAL)** —— 付一次即永久有效, 不限视频数量、不限调用次数。
  与按量模式的关键区别:
  - resource_id 不是"视频指纹", 而是固定的买断资源标识(与具体视频无关)
  - 首次付款后服务端签发长效 license(token = PASS_<out_trade_no>), 本脚本持久化到
    `~/.ai-live-clip/license.json`(权限 600)
  - 之后每次 probe 自动带 `X-License` 头 -> 服务端直通放行, **不再产生账单**

本文件随**付费版**技能包分发(位于 scripts/)。
包内**不含任何密钥/私钥**, 不自行签名、不自行实现付款 —— 只做编排;
后端计费服务独立部署, 源码不随技能包分发。

职责边界(按 SkillHub/支付宝官方要求):
  本脚本只做**编排**, 不自行实现付款、不接触密钥、不自行签名。
  真实支付交给买家侧官方 CLI `alipay-bot`(由 `npx -y @alipay/agent-payment@latest install` 安装)。

子命令(对齐官方 probe/pay/complete 三段式):
  probe    [--video <路径>]          已买断 -> 直通 200; 未付款 -> 402, 落 bill 状态文件
  pay      [--session-id ID]         把账单交给 alipay-bot 402-buyer-pay, 展示支付入口等用户付款
  complete [--out-trade-no NO]       查单成功后自动回源取授权并落盘(不重新下单)
  complete --proof <B64>             直连重试(仅 MOCK/离线联调用, 绕过 alipay-bot)
  status   --out-trade-no NO         查询订单状态(排障用)
  license                            查看本机买断授权状态(不发网络请求)

★ 已修缺陷(2026-10-29):
  - complete 官方分支原先只查单不落授权(与 SKILL.md 表述不符), 现查单成功后自动回源取 token 落盘
  - 服务端 200 未返回 token 时, 按服务端签发规则 PASS_<out_trade_no> 兜底, 避免"买断失效"
  - 网络异常/5xx 与"未付款(402)"分离: 已买断用户遇到服务端故障走离线宽限, 不会被卡死
  - URLError(连接被拒/超时)原先未捕获会直接 traceback, 现已捕获并计为网络不可达
  - 金额统一为 19.90 元常量, 402 账单金额与之一致性校验

环境变量:
  AI_CLIP_BILLING_URL      计费服务地址(默认 http://127.0.0.1:8787)
  AI_LIVE_CLIP_WORKDIR     工作区根目录(.pay_cache 落这里; 不传则用 cwd)
  AI_CLIP_PRICE_CNY        买断价格(默认 19.90, 三处价格必须与之一致)
  AI_CLIP_HTTP_RETRIES     网络异常重试次数(默认 2)
  AI_CLIP_HTTP_RETRY_SLEEP 重试间隔秒(默认 3)
  AI_CLIP_OFFLINE_GRACE    已买断时服务端故障是否离线放行(默认 1, 置 0 关闭)
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
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import licensing  # noqa: E402  本地授权(零服务端)核心

# ★ 计费模式: 配了服务端地址(环境变量 AI_CLIP_BILLING_URL 或 config.json 的 service_url)
#   就走 402 沙箱服务端模式; 都没配则退回本地授权模式(离线兜底, 靠作者签发的授权码)
_cfg = licensing.load_config()
SERVICE_URL = (os.environ.get("AI_CLIP_BILLING_URL", "").strip()
               or str(_cfg.get("service_url") or "").strip())
SERVER_MODE = bool(SERVICE_URL)

BILLING_URL = (SERVICE_URL or "http://127.0.0.1:8787").rstrip("/")
# ★ 买断资源端点(沙箱服务端 main.py 里注册的就是它)
RESOURCE_PATH = os.environ.get("AI_CLIP_RESOURCE_PATH", "/api/license")
# ★ 沙箱应用网关要求携带 sharecode 访问参数(平台分配, 见 config.json 的 service_sharecode)
SHARECODE = (os.environ.get("AI_CLIP_SHARECODE") or "").strip() or str(
    _cfg.get("service_sharecode") or "").strip()


def _mk_url(path: str) -> str:
    """拼接资源 URL 并附加沙箱 sharecode 访问参数(如有)。"""
    u = BILLING_URL + path
    if SHARECODE:
        u += ("&" if "?" in u else "?") + "sharecode=" + SHARECODE
    return u


RESOURCE_URL = os.environ.get("AI_CLIP_RESOURCE_URL") or _mk_url(RESOURCE_PATH)
WORKDIR = os.environ.get("AI_LIVE_CLIP_WORKDIR") or os.getcwd()
STATE_DIR = os.path.join(WORKDIR, ".pay_cache")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
BILL_FILE = os.path.join(STATE_DIR, "payment-needed")
# 官方 CLI 回传的支付凭证(Payment-Proof); 仅本地排障/复用, 权限收紧
PROOF_FILE = os.path.join(STATE_DIR, "payment-proof")

# ★ 买断资源标识: 固定值, 与具体视频无关
LICENSE_RESOURCE_ID = os.environ.get("LICENSE_RESOURCE_ID", "LICENSE_LIVECLIP")
# ★ 买断授权持久化: 放在用户目录(换工作区/换视频仍然有效), 不走 .pay_cache
#   AI_CLIP_LICENSE_FILE 仅用于联调/测试覆盖, 正常不需要设置
LICENSE_FILE = (os.environ.get("AI_CLIP_LICENSE_FILE")
                or os.path.join(os.path.expanduser("~"), ".ai-live-clip", "license.json"))

BOT = os.environ.get("ALIPAY_BOT_CMD", "alipay-bot")
BODY_FLAG = os.environ.get("ALIPAY_BOT_BODY_FLAG", "")       # 官方 CLI 无此参数, 默认关闭
HEADERS_FLAG = os.environ.get("ALIPAY_BOT_HEADERS_FLAG", "")  # 同上

# ★ 三处价格必须一致: 支付宝服务登记价 = 服务端 402 账单金额 = SkillHub 展示价
PRICE_CNY = os.environ.get("AI_CLIP_PRICE_CNY", "19.90")
# 网络异常重试(仅针对连接失败/5xx, 402/200 不重试)
HTTP_RETRIES = int(os.environ.get("AI_CLIP_HTTP_RETRIES", "2"))
HTTP_RETRY_SLEEP = float(os.environ.get("AI_CLIP_HTTP_RETRY_SLEEP", "3"))
# 已买断用户遇到服务端故障时的离线宽限
OFFLINE_GRACE = os.environ.get("AI_CLIP_OFFLINE_GRACE", "1") != "0"

# 网络不可达/连接被拒时的伪状态码
STATUS_NETWORK = 0


# ---------------- 基础工具 ----------------

def resource_id_for(video_path: str | None = None) -> str:
    """★ 买断模式: 资源标识固定, 与视频无关。

    (按量模式曾按「路径+体积」生成视频指纹; 买断后一次付费覆盖全部视频,
      再按视频生成 resource_id 会导致每个新视频都要重新付费, 故改为固定值。
      参数保留仅为兼容旧调用(不参与计算)。)
    """
    return LICENSE_RESOURCE_ID


def check_amount(amount):
    """三处价格一致性校验(仅告警, 不阻断)。"""
    try:
        if abs(float(amount) - float(PRICE_CNY)) > 1e-6:
            print(f"[警告] 账单金额 {amount} 与约定价 {PRICE_CNY} 不一致 "
                  f"—— 支付宝登记价 / 服务端账单 / SkillHub 展示价必须三处一致")
            return False
    except (TypeError, ValueError):
        pass
    return True


def unreachable(status: int, body: dict) -> bool:
    """网络不可达或服务端 5xx: 与「未付款(402)」严格区分。"""
    if status == STATUS_NETWORK or status >= 500:
        detail = body.get("raw", "") if isinstance(body, dict) else ""
        print(f"[闸口不可用] HTTP {status or '网络不可达'} —— 计费服务无响应: {detail}")
        print(f"  服务地址 {BILLING_URL}")
        print("  可能原因: 服务端未部署/未启动, 或 AI_CLIP_BILLING_URL 未指向公网地址")
        return True
    return False


def load_license() -> dict:
    """读取本机买断授权(不存在/损坏返回空字典)。"""
    try:
        with open(LICENSE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_license(payload: dict):
    """持久化买断授权(权限 600)。"""
    _write_private(LICENSE_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


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
    _harden_windows(path)


def _harden_windows(path: str):
    """Windows 上 os.chmod 是 no-op, 用 icacls 收紧到当前用户(失败静默)。"""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r",
             f"{os.environ.get('USERNAME', '*')}:(R,W)"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def _http_json_once(method: str, path: str, body: dict | None = None,
                    headers: dict | None = None, params: dict | None = None):
    url = _mk_url(path)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
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
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 连接被拒 / 超时 / DNS 失败: 原先未捕获会直接 traceback
        reason = getattr(e, "reason", e)
        return STATUS_NETWORK, {}, {"raw": f"网络不可达: {reason}"}


def http_json(method: str, path: str, body: dict | None = None,
              headers: dict | None = None, params: dict | None = None):
    """返回 (status, headers, json_body)。402 等非 2xx 也正常返回, 不抛异常。

    网络不可达返回 STATUS_NETWORK(0); 对网络故障与 5xx 做有限重试。
    """
    last = (STATUS_NETWORK, {}, {"raw": "未发起请求"})
    for attempt in range(HTTP_RETRIES + 1):
        last = _http_json_once(method, path, body, headers, params)
        status = last[0]
        if status == STATUS_NETWORK or status >= 500:
            if attempt < HTTP_RETRIES:
                print(f"[重试 {attempt + 1}/{HTTP_RETRIES}] HTTP {status or '网络不可达'}, "
                      f"{HTTP_RETRY_SLEEP:g}s 后重连 {BILLING_URL}")
                time.sleep(HTTP_RETRY_SLEEP)
                continue
        return last
    return last


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


def run_bot_capture(args: list[str]):
    """执行官方 CLI 并捕获输出(用于提取 Payment-Proof)。返回 (退出码, 输出文本)。"""
    print(f"[执行] {BOT} {' '.join(args)}", flush=True)
    try:
        proc = subprocess.run([BOT, *args], check=False,
                              capture_output=True, text=True, errors="replace")
    except OSError as e:
        print(f"[执行失败] {e}")
        return 127, ""
    out = (proc.stdout or "") + (proc.stderr or "")
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    return proc.returncode, out


def extract_proof(text: str) -> str:
    """从官方 CLI 输出里尽力提取 Payment-Proof。

    不同版本 alipay-bot 的输出格式不一致(有的给 JSON 里的 payment_proof, 有的给
    `Payment-Proof: xxx` 行), 这里做多重兜底; 提取不到就交给用户手动传 --proof。
    """
    pats = [
        r'"payment_proof"\s*:\s*"([^"]{16,})"',
        r"Payment-Proof\s*[:=]\s*([A-Za-z0-9_\-]{16,})",
        r"payment-proof\s*[:=]\s*([A-Za-z0-9_\-]{16,})",
    ]
    for p in pats:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""


def _authorized(body: dict) -> int:
    """服务端 200 -> 落盘买断授权。

    ★ 首选服务端签发的 `license_key`(RSA 授权码, 之后可离线验签);
      没有 license_key 的旧服务端才退回 token 模式。
    """
    key = (body.get("license_key") or "").strip()
    if key:
        ok, payload, reason = licensing.verify_key(key)
        if not ok:
            print(f"[未取得授权] 服务端返回的授权码验签失败: {reason}")
            print("  服务端 license_private_key 与本包 config.json 的公钥不是同一对密钥")
            return 4
        save_license({
            "mode": "server_license",
            "key": key,
            "token": payload.get("oid", ""),
            "out_trade_no": payload.get("oid", ""),
            "trade_no": body.get("trade_no", ""),
            "amount": payload.get("amt", "") or PRICE_CNY,
            "machine": payload.get("mid", "*"),
            "perpetual": licensing.is_perpetual(payload),
            "expires_at": payload.get("exp", ""),
            "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        })
        print(f"[买断授权已保存] {LICENSE_FILE}")
        print(f"[已买断] 订单 {payload.get('oid', '')}  "
              f"金额 {payload.get('amt', '') or PRICE_CNY} CNY  "
              f"机器 {payload.get('mid', '*')}")
        if body.get("license_scope"):
            print(f"  授权范围: {body['license_scope']}")
        print("永久有效 —— 之后 probe 走本地验签直通, 不再请求服务端、不再产生费用")
        print("可以继续执行本地流水线: asr_pipeline -> segmenter -> cut_pipeline")
        return 0

    out_no = body.get("out_trade_no", "")
    token = body.get("token") or ""
    derived = False
    if not token and out_no:
        # 服务端签发规则为 PASS_<out_trade_no>; 200 未回传 token 时兜底, 避免买断失效
        token = f"PASS_{out_no}"
        derived = True
        print("[警告] 服务端 200 响应未返回 token, 已按 PASS_<out_trade_no> 兜底推导")
        print("       若服务端不接受该 token, 请让服务端在 200 响应中回传 token 字段")
    if token:
        save_license({
            "token": token,
            "token_derived": derived,
            "out_trade_no": out_no,
            "trade_no": body.get("trade_no", ""),
            "amount": body.get("amount", "") or PRICE_CNY,
            "perpetual": True,
            "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        })
        print(f"[买断授权已保存] {LICENSE_FILE}")
    else:
        print("[未取得授权] 200 响应中既无 token 也无订单号, 授权无法落盘 —— 本次不放行")
        print("  请让服务端在 200 响应中回传 token 与 out_trade_no 字段")
        return 4
    print(f"[已买断] 订单 {out_no}  金额 {body.get('amount') or PRICE_CNY} CNY")
    print(f"license_token = {token}")
    print("永久有效, 不限视频数量 —— 之后任意视频直接执行 probe 即可放行")
    print("可以继续执行本地流水线: asr_pipeline -> segmenter -> cut_pipeline")
    return 0


def license_body() -> dict:
    """买断资源请求体。

    ★ 协议硬约束: 带 Payment-Proof 重试时 **Body 必须与首次完全一致**, 所以请求体
      必须可复现(不能带时间戳/随机数)。这里只有机器码与可选的历史授权码。
      带 license_key 时服务端走「换机恢复」分支, 不会重新出账。
    """
    lic = load_license()
    body = {"machine_code": licensing.machine_code()}
    if lic.get("key"):
        body["license_key"] = lic["key"]
    return body


def request_resource(headers: dict | None = None, body: dict | None = None):
    """请求买断资源接口; 返回 (status, headers, body)。"""
    return http_json("POST", RESOURCE_PATH,
                     body if body is not None else license_body(),
                     headers=headers)


def offline_grant() -> bool:
    """服务端不可达/5xx 时, 已买断用户的离线宽限。"""
    lic = load_license()
    if not OFFLINE_GRACE or not lic.get("token"):
        return False
    if not lic.get("perpetual", False):
        return False
    print("[离线宽限] 计费服务暂不可用, 但本机已有买断授权 —— 本次放行, 不阻断流水线")
    print(f"  授权订单 {lic.get('out_trade_no', '')}  保存于 {lic.get('saved_at', '')}")
    print("  服务恢复后可执行 probe 复核; 设 AI_CLIP_OFFLINE_GRACE=0 可关闭本宽限")
    return True


# ---------------- 本地授权模式(零服务端) ----------------

def local_status():
    """本地验签本机授权码 -> (是否通过, payload, 原因)。不发任何网络请求。"""
    key = load_license().get("key", "")
    if not key:
        return False, {}, "本机尚未激活(无授权码)"
    return licensing.verify_key(key)


def _print_buy_guide(reason: str):
    cfg = licensing.load_config()
    price = cfg.get("price_cny", PRICE_CNY)
    print(f"[未激活] {reason}")
    print(f"  买断制: {price} 元一次性, 永久有效, 不限视频数量与调用次数")
    alipay = cfg.get("alipay") or {}
    if cfg.get("billing_channel") == "alipay_a2m_self":
        # 402 沙箱服务端: 付款入口由账单经 alipay-bot 在对话内生成
        print(f"  付款方式: 支付宝 AI 付(402 买断), 服务端 {BILLING_URL}")
        print("  流程: probe 出账 -> pay 在对话内付款 -> complete 取回授权码 -> 之后永久有效")
        print("  服务端不可用时, 可向作者索取授权码后执行:")
        print("    python payment_gate.py activate <授权码>")
    elif cfg.get("billing_channel") == "skillhub_platform_managed":
        # 平台代收: 收款由 SkillHub 在调用链路托管, 技能侧不持有任何收款链接
        print("  付款方式: SkillHub 平台代收(支付宝 AI 付), 对话内完成, 不跳出页面、无需自建服务端")
        print("  首次付款后本机留下授权记录, 之后 probe 见授权即直通放行 —— 买断由此实现")
        print("  若平台未自动下发支付入口, 向作者索取授权码后执行:")
        print("    python payment_gate.py activate <授权码>")
    else:
        if alipay.get("pay_url"):
            print(f"  支付宝付款: {alipay['pay_url']}")
        if alipay.get("pay_note"):
            print(f"  {alipay['pay_note']}")
        print("  拿到授权码后激活: python payment_gate.py activate <授权码>")
    print(f"  本机机器码(需绑定机器时发给作者): {licensing.machine_code()}")


def _print_deps(reason: str) -> bool:
    """已装授权码但缺依赖时给出安装指引, 返回 True 表示已处理。"""
    if "安装" not in reason:
        return False
    print(f"[依赖缺失] {reason}")
    print("  pip install cryptography")
    print("  (或用已装好依赖的虚拟环境执行本脚本, 例如 funasr-clip 环境)")
    return True


def _print_local_ok(payload: dict) -> int:
    print(f"[已买断] 订单 {payload.get('oid', '')}  "
          f"金额 {payload.get('amt', '')} CNY  机器 {payload.get('mid', '*')}")
    print(f"  永久有效 {licensing.is_perpetual(payload)}"
          + ("" if not payload.get("exp") else f"  到期 {payload['exp']}"))
    print("  本次为本地验签放行 —— 未请求服务端, 不产生费用")
    print("可以继续执行本地流水线: asr_pipeline -> segmenter -> cut_pipeline")
    return 0


def cmd_probe_local(args):
    ok, payload, reason = local_status()
    if ok:
        return _print_local_ok(payload)
    if _print_deps(reason):
        return 4
    _print_buy_guide(reason)
    return 402


def cmd_activate(args):
    code = (args.code or "").strip()
    ok, payload, reason = licensing.verify_key(code)
    if not ok:
        print(f"[激活失败] {reason}")
        print("  授权码应为 LIVECLIP1.xxx.yyy 一整行, 注意不要截断或换行")
        return 2
    save_license({
        "mode": "local_license",
        "key": code,
        "token": payload.get("oid", ""),
        "out_trade_no": payload.get("oid", ""),
        "amount": payload.get("amt", ""),
        "machine": payload.get("mid", "*"),
        "perpetual": licensing.is_perpetual(payload),
        "expires_at": payload.get("exp", ""),
        "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    })
    print(f"[已激活] 订单 {payload.get('oid', '')}  金额 {payload.get('amt', '')} CNY")
    print(f"  机器 {payload.get('mid', '*')}  "
          f"{'永久有效' if licensing.is_perpetual(payload) else '到期 ' + str(payload.get('exp'))}")
    print(f"  授权文件 {LICENSE_FILE}")
    print("可以继续执行本地流水线: asr_pipeline -> segmenter -> cut_pipeline")
    return 0


def cmd_machine_code(args):
    print(f"machine_code = {licensing.machine_code()}")
    print("把它发给作者可签发绑定本机的授权码; 不传机器码则签发不限机器的通用码")
    return 0


# ---------------- 子命令 ----------------

def cmd_probe(args):
    # ★★ 买断直通(最关键的一条): 本机已有有效授权码 -> 纯本地验签放行,
    #    不请求服务端、不出账。用户只为"第一次"付钱, 之后永远不再产生费用。
    ok, payload, reason = local_status()
    if ok:
        return _print_local_ok(payload)
    if _print_deps(reason):
        return 4
    if not SERVER_MODE:
        _print_buy_guide(reason)
        return 402

    body = license_body()
    status, headers, resp = request_resource(body=body)
    if status == 200:
        return _authorized(resp)
    if status == 402:
        pn = hget(headers, "Payment-Needed")
        if not pn:
            print(f"[异常] 返回 402 但缺少 Payment-Needed 响应头: {resp}")
            return 402
        bill = decode_payment_needed(pn)
        proto = bill["protocol"]
        check_amount(proto.get("amount"))
        _write_private(BILL_FILE, pn)
        save_state({
            "resource_id": proto.get("resource_id", ""),
            "resource_url": RESOURCE_URL,
            "method": "POST",
            # ★ 取货重试时 Body 必须与首次完全一致, 原样存下来
            "body": body,
            "out_trade_no": proto["out_trade_no"],
            "amount": proto["amount"],
            "currency": proto["currency"],
            "pay_before": proto["pay_before"],
            "goods_name": (bill.get("method") or {}).get("goods_name", ""),
            "session_id": args.session_id or proto["out_trade_no"],
            # ★ 官方产品接入指南: 402 的 amtPaymentLink 头是预下单号, 一并落盘便于排障
            "amt_pre_pay_id": hget(headers, "amtPaymentLink")
                              or proto.get("amt_pre_pay_id", ""),
        })
        print("[402 需支付] 账单已保存, 下一步执行 pay:")
        print(f"  金额 {proto['amount']} {proto['currency']}  订单号 {proto['out_trade_no']}")
        print(f"  支付截止 {proto['pay_before']}")
        _amt = hget(headers, "amtPaymentLink") or proto.get("amt_pre_pay_id", "")
        _mock = bool(_amt) and str(_amt).startswith(("MOCKPRE", "LOCALFALLBACK"))
        if _mock:
            print(f"  [警告] 预下单号是本地模拟值({_amt[:12]}…), 这笔账单付不了款, "
                  f"仅用于联调; 生产需真实 amt_pre_pay_id")
        # ★ 服务端预下单失败时会降级出 402(不硬失败), 但把支付宝错误原文放进响应体
        #   precreate_error, 且不下发 amtPaymentLink。这里必须打印出来, 否则买家侧
        #   只看到下游模糊报错(如卖家标识不匹配), 容易误判成卖家配置问题而瞎改。
        if not _amt or _mock:
            _pre_err = resp.get("precreate_error") if isinstance(resp, dict) else None
            if _pre_err:
                print(f"  [服务端预下单失败] {_pre_err}")
                print("  ⛔ 支付宝侧没有这笔预订单, 这笔账单付不了款; "
                      "请把订单号与上面错误原文发给作者, 不要反复重试 pay")
            else:
                print("  [警告] 服务端未下发真实预下单号(amtPaymentLink), 付款可能无法完成; "
                      "请把订单号发给作者排障")
        print(f"  python payment_gate.py pay")
        return 402
    if unreachable(status, body):
        if offline_grant():
            return 0
        return 3
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
    # 官方 402-buyer-pay 只认 --session-id/--file/--resource-url/--method/--intent-summary
    # 五个参数。早期版本猜测的 --body/--headers 官方 CLI 并不支持, 默认不下传;
    # 若你用的 CLI 版本确实需要, 可用 ALIPAY_BOT_BODY_FLAG 环境变量开启。
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
    proof = (args.proof or "").strip()

    # 未显式传凭证时, 复用上一轮从官方 CLI 输出里捕获到的那份
    if not proof and os.path.exists(PROOF_FILE):
        try:
            with open(PROOF_FILE, encoding="utf-8") as f:
                proof = f.read().strip()
        except OSError:
            proof = ""
        if proof:
            print(f"[复用已保存凭证] {PROOF_FILE}")

    # ★ 带凭证取货: Body 必须与首次完全一致(协议硬约束), 否则服务端无法映射回原订单
    if proof:
        body = state.get("body") or license_body()
        status, hdrs, resp = request_resource(
            headers={"Payment-Proof": proof,
                     "Idempotency-Key": out_no or state.get("out_trade_no") or "cli"},
            body=body)
        if status == 200:
            # ★ 官方判据: 200 之外还须 Payment-Validation 响应头, 且 body.fulfillment_confirmed=true
            if not hget(hdrs, "Payment-Validation"):
                print("[未放行] HTTP 200 但缺少 Payment-Validation 响应头 —— 履约未完成")
                print("  官方协议: 只有 200 + Payment-Validation 且 fulfillment_confirmed=true 才放行")
                return 502
            if isinstance(resp, dict) and resp.get("fulfillment_confirmed") is not True:
                print("[未放行] 履约未确认(fulfillment_confirmed != true), 不交付授权")
                return 502
            return _authorized(resp)
        if status == 502:
            print("[履约确认未完成] 授权码已生成, 但服务端未能向支付宝确认履约")
            print("  用同一凭证重试本命令即可 —— 幂等, 不会重复扣费")
            return status
        if unreachable(status, resp):
            print("[提示] 服务端暂不可用; 恢复后重试本命令即可补领授权(不会重复扣费)")
            return 3
        print(f"[未放行] HTTP {status}: {resp}")
        print("  若提示验付未通过: 确认订单已完成支付, 且凭证与本次订单对应")
        return status

    if not out_no:
        print("[错误] 需要 --out-trade-no, 或先执行 probe 生成状态")
        return 2

    # 查原订单(不重新下单); 成功后再用凭证取货
    rc, out = run_bot_capture([
        "402-query-payment-status",
        "--out-shake-no", out_no,
        "--resource-url", state.get("resource_url", RESOURCE_URL),
        "--method", state.get("method", "POST"),
    ])
    if rc != 0:
        print(f"[未完成] 查单退出码 {rc}; 确认支付后重试, "
              f"或执行 status --out-trade-no {out_no} 排障")
        return rc

    got = extract_proof(out)
    if got:
        _write_private(PROOF_FILE, got)
        print("[已取得支付凭证] 正在回源领取买断授权 ...")
        return cmd_complete(type("A", (), {"proof": got, "out_trade_no": out_no})())
    print("[提示] 官方 CLI 未回传 Payment-Proof, 无法回源取货")
    print("  请从上面输出里找到支付凭证, 然后执行:")
    print("    python payment_gate.py complete --proof <凭证>")
    return 5


def cmd_status(args):
    status, _, body = http_json(
        "GET", RESOURCE_PATH + "/status", params={"out_trade_no": args.out_trade_no})
    print(f"HTTP {status}: {json.dumps(body, ensure_ascii=False)}")
    return 0 if status == 200 else status


def cmd_license(args):
    """查看本机买断授权(不发网络请求)。"""
    if not SERVER_MODE:
        ok, payload, reason = local_status()
        if not ok:
            if _print_deps(reason):
                return 4
            cfg = licensing.load_config()
            print(f"[未激活] {reason}")
            print(f"  买断价 {cfg.get('price_cny', PRICE_CNY)} 元(一次性, 永久有效)")
            print("  激活: python payment_gate.py activate <授权码>")
            return 1
        print(f"[已买断] 订单 {payload.get('oid', '')}  金额 {payload.get('amt', '')} CNY")
        print(f"  机器 {payload.get('mid', '*')}  "
              f"{'永久有效' if licensing.is_perpetual(payload) else '到期 ' + str(payload.get('exp'))}")
        print(f"  激活于 {load_license().get('saved_at', '')}")
        print(f"  授权文件 {LICENSE_FILE}")
        print("  模式: 本地授权(零服务端, 支付宝一次性买断)")
        return 0
    lic = load_license()
    if not lic.get("token"):
        print(f"[未买断] 本机无授权文件: {LICENSE_FILE}")
        print(f"执行 probe 发起买断支付({PRICE_CNY} 元, 永久有效)")
        return 1
    print(f"[已买断] token = {lic['token']}")
    print(f"  订单号 {lic.get('out_trade_no', '')}   金额 {lic.get('amount', '')} CNY")
    print(f"  交易号 {lic.get('trade_no', '')}   永久有效 {lic.get('perpetual', False)}")
    if lic.get("token_derived"):
        print("  ⚠ token 为本地推导值(服务端 200 未回传 token), 如放行失败请联系服务端修复")
    print(f"  保存于 {lic.get('saved_at', '')}")
    print(f"  授权文件 {LICENSE_FILE}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="ai-live-clip 付费闸口(官方 alipay-bot 编排)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="请求资源接口, 未付款返回 402 并保存账单")
    p.add_argument("--video", required=False,
                   help="原视频路径(买断模式下不参与计费, 仅为兼容旧调用保留)")
    p.add_argument("--session-id", help="会话 ID(默认用订单号)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("pay", help="把账单交给官方 alipay-bot, 展示支付入口等待付款")
    p.add_argument("--session-id", help="会话 ID(默认用 probe 时保存的值)")
    p.set_defaults(func=cmd_pay)

    p = sub.add_parser("complete", help="付款后查原订单并取得资源(不重新下单)")
    p.add_argument("--out-trade-no", help="402 账单里的商户订单号")
    p.add_argument("--proof", help="[仅 MOCK/离线联调] 直接带 Payment-Proof 重试")
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("activate", help="[零服务端] 用作者签发的授权码激活本机")
    p.add_argument("code", help="授权码(形如 LIVECLIP1.xxx.yyy, 一整行)")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("machine-code", help="[零服务端] 显示本机机器码(用于签发绑定机器的授权码)")
    p.set_defaults(func=cmd_machine_code)

    p = sub.add_parser("status", help="查询订单状态(排障用)")
    p.add_argument("--out-trade-no", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("license", help="查看本机买断授权状态(不发网络请求)")
    p.set_defaults(func=cmd_license)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
