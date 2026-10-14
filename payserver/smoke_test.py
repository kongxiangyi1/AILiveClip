# -*- coding: utf-8 -*-
"""全链路冒烟(MOCK, 无需密钥): 覆盖 SkillHub 付费检查的 5 个缺失项。

402账单下发 -> 模拟支付 -> 携凭证重试验付放行 -> 幂等重试 -> 防重放
-> 按量新订单 -> 订单持久化 -> 仅带凭证(不回传订单号)仍能放行

用法: 在 payserver/ 目录执行  python smoke_test.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
TMP = tempfile.mkdtemp(prefix="payserver_smoke_")

os.environ.update(ALIPAY_MOCK="1", BILL_DB=os.path.join(TMP, "orders.db"),
                  AI_CLIP_BILLING_URL=BASE, AI_LIVE_CLIP_WORKDIR=TMP)
sys.path.insert(0, os.path.join(HERE, "client"))
import payment_gate as gate  # noqa: E402  (必须在环境变量之后导入)

FAKE_VIDEO = os.path.join(TMP, "0710.ts")
with open(FAKE_VIDEO, "wb") as f:
    f.write(b"x" * 1024)


def wait_health(timeout=30):
    for _ in range(timeout * 5):
        try:
            with urllib.request.urlopen(BASE + "/healthz", timeout=2) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.2)
    raise SystemExit("服务未就绪")


PROD_GW = "https://openapi.alipay.com/gateway.do"

# 子进程片段: 用指定环境导入并跑启动期硬校验, 被拒时打印 REJECT: 原因
GUARD_SNIPPET = (
    "import sys; sys.path.insert(0, %r)\n"
    "import alipay_client as ac\n"
    "try:\n"
    "    ac.assert_runtime_config()\n"
    "except RuntimeError as e:\n"
    "    print('REJECT:', e); sys.exit(0)\n"
    "print('NO_REJECT')\n"
) % HERE


def ns(**kw):
    return type("Args", (), kw)()


def mock_pay(out_no):
    """模拟支付宝支付成功, 返回客户端可用的 Payment-Proof(Base64)。"""
    req = urllib.request.Request(
        f"{BASE}/api/v1/dev/mock-pay?out_trade_no={out_no}", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())["payment_proof"]


def main():
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--port", str(PORT),
         "--log-level", "warning"], cwd=HERE)
    try:
        h = wait_health()
        assert h["mock"] is True, h
        print("[1] healthz OK:", h)

        # ① 402账单下发
        rc = gate.cmd_probe(ns(video=FAKE_VIDEO, session_id=None))
        assert rc == 402, f"期望402, 实得{rc}"
        state = gate.load_state()
        out_no = state["out_trade_no"]
        print(f"[2] 402账单 OK: 订单={out_no} 金额={state['amount']}CNY "
              f"截止={state['pay_before']}")

        # 模拟支付(生产环境由支付宝支付能力完成)
        proof = mock_pay(out_no)
        print("[3] 模拟支付 OK: proof", len(proof), "bytes")

        # ② 携带凭证重试 + ③ 验付调用
        rc = gate.cmd_complete(ns(proof=proof, out_trade_no=out_no))
        assert rc == 0, f"期望放行, 实得{rc}"
        print("[4] 验付放行 OK")

        # ④b 成功响应必须回 Payment-Validation 头(官方联调成功证据)
        st, hdrs, b2 = gate.http_json(
            "POST", "/api/v1/resource",
            {"resource_id": state["resource_id"], "out_trade_no": out_no},
            headers={"Payment-Proof": proof})
        assert st == 200, b2
        pv = gate.hget(hdrs, "Payment-Validation")
        assert pv, "缺少 Payment-Validation 响应头(官方判为联调失败)"
        valid = json.loads(base64.urlsafe_b64decode(pv + "=" * (-len(pv) % 4)))
        assert valid["validated"] is True and valid["out_trade_no"] == out_no, valid
        assert b2.get("resource_id") and b2.get("content"), "资源/内容不得为空"
        print("[4b] Payment-Validation 回执 OK: validated=True "
              f"resource={valid['resource_id']}")

        # ④ 幂等: 同一凭证重试 -> 同一授权, 不重复扣费
        rc = gate.cmd_complete(ns(proof=proof, out_trade_no=out_no))
        assert rc == 0, f"幂等重试期望0, 实得{rc}"
        print("[5] 幂等重试 OK(同一凭证返回同一授权)")

        # ⑤ 防重放: 伪造不同凭证攻击已结算订单 -> 409
        evil = base64.b64encode(json.dumps(
            {"protocol": {"payment_proof": "EVIL", "trade_no": "MOCK_OTHER"},
             "method": {}}).encode()).decode()
        rc = gate.cmd_complete(ns(proof=evil, out_trade_no=out_no))
        assert rc == 409, f"防重放期望409, 实得{rc}"
        print("[6] 防重放 OK(不同凭证攻击已结算订单被拒)")

        # 按量计费: 再次调用下发新订单
        rc = gate.cmd_probe(ns(video=FAKE_VIDEO, session_id=None))
        assert rc == 402
        out_no2 = gate.load_state()["out_trade_no"]
        assert out_no2 != out_no, "应生成新订单"
        print(f"[7] 按量计费 OK: 第二次调用下发新订单 {out_no2}")

        st, _, body = gate.http_json("GET", f"/api/v1/orders/{out_no}")
        assert st == 200
        assert body["fulfill_status"] == "FULFILLED", body
        assert body["pay_before"], "订单应记录 pay_before(防旧账单重放)"
        print(f"[8] 订单持久化 OK: {out_no} -> {body['fulfill_status']} "
              f"(pay_before={body['pay_before'][:19]})")

        # ⑥ 官方 CLI 场景: Payment-Proof 只含 payment_proof/trade_no, 客户端不回传订单号
        proof2 = mock_pay(out_no2)
        rc = gate.cmd_complete(ns(proof=proof2, out_trade_no=None))
        assert rc == 0, f"仅带凭证时也应放行, 实得{rc}"
        print("[9] 免回传订单号放行 OK(服务端按 trade_no/验付结果反查订单)")

        # ⑦-⑨ 生产旁路防护: 违规配置组合必须硬失败(官方 checklist「支付校验无旁路」)
        cases = [
            ("生产网关下开 MOCK 被拒",
             dict(ALIPAY_MOCK="1", ALIPAY_GATEWAY=PROD_GW,
                  ALIPAY_SERVICE_ID="api_mock_service_id"), True),
            ("生产网关仍用 api_mock_service_id 被拒",
             dict(ALIPAY_MOCK="0", ALIPAY_GATEWAY=PROD_GW,
                  ALIPAY_SERVICE_ID="api_mock_service_id"), True),
            ("生产缺私钥等配置被拒",
             dict(ALIPAY_MOCK="0", ALIPAY_GATEWAY=PROD_GW,
                  ALIPAY_SERVICE_ID="API_real_0000000000"), True),
            ("生产配置齐备时放行",
             dict(ALIPAY_MOCK="0", ALIPAY_GATEWAY=PROD_GW,
                  ALIPAY_SERVICE_ID="API_real_0000000000",
                  ALIPAY_APP_ID="2021000000000000",
                  ALIPAY_APP_PRIVATE_KEY="ZmFrZQ==",
                  ALIPAY_PUBLIC_KEY="ZmFrZQ==",
                  ALIPAY_SELLER_ID="2088000000000000"), False),
        ]
        for i, (name, env, expect_reject) in enumerate(cases, start=10):
            child = dict(os.environ)
            for k in ("ALIPAY_APP_ID", "ALIPAY_APP_PRIVATE_KEY",
                      "ALIPAY_PUBLIC_KEY", "ALIPAY_SELLER_ID"):
                child.pop(k, None)
            child.update(env)
            p = subprocess.run([sys.executable, "-c", GUARD_SNIPPET],
                               cwd=HERE, env=child, capture_output=True, text=True)
            rejected = p.stdout.startswith("REJECT:")
            assert rejected == expect_reject, (
                f"{name}: 期望reject={expect_reject}, 实得 stdout={p.stdout!r} "
                f"stderr={p.stderr[-300:]!r}")
            print(f"[{i}] {name} OK"
                  + (f" -> {p.stdout[7:].strip()[:60]}" if rejected else ""))

        print("\n全部通过 (13/13) - 402/凭证重试/验付/履约/幂等持久化/生产旁路防护 全通过")
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
