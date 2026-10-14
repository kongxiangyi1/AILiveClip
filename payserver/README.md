# payserver — ai-live-clip 付费改造(SkillPay / 支付宝 AI 按量付费)

对应 SkillHub 提交检查的 5 个缺失项, 全部在服务端落地:

| 平台缺失项 | 本仓库实现 |
|---|---|
| 402账单下发 | `app.py` `/api/v1/resource` 无凭证时返回 `402` + `Payment-Needed` 头(`alipay_client.build_bill` 生成两层账单 JSON + RSA2 加签) |
| 携带凭证重试 | 客户端 `client/payment_gate.py complete` 复用原订单重试同一资源请求(不重新下单; 离线联调可用 `--proof`) |
| 验付调用 | `alipay_client.verify_proof` 调 `alipay.aipay.agent.payment.verify`, 并核对 active / amount / out_trade_no / resource_id / trade_no |
| 履约确认 | **资源生成 → confirm 成功 → 才交付**; 失败返回 502, 允许用同一凭证重试(不重复扣费) |
| 成功回执 | 交付响应带 `Payment-Validation` 头(Base64URL: trade_no/out_trade_no/validated/resource_id) |
| 订单持久化与幂等 | `orders.py` SQLite: out_trade_no 主键 / trade_no UNIQUE / proof_hash UNIQUE(防重放), `prepare_fulfillment` 原子化(资源只生成一次) |
| 无支付旁路 | 启动期 `assert_runtime_config()` 硬校验: 生产网关+MOCK / 占位 serviceId / 缺私钥 → 拒绝启动 |

**当前状态: 服务端已通过官方代码开发校验(部分通过, 仅沙箱配置未完成); MOCK 模式可本地全链路跑通; 上生产前需要签约支付宝 + 部署(见第 2/3 节)。**

**当前状态: 骨架已完成, MOCK 模式可本地全链路跑通; 上生产前需要签约支付宝 + 部署(见第 2/3 节)。**

---

## 1. 本地联调(MOCK, 无需任何密钥)

```powershell
# 终端1: 起服务(在 payserver/ 目录)
$env:ALIPAY_MOCK="1"; $env:BILL_DB="data/orders.db"
python -m uvicorn app:app --port 8787

# 终端2: 客户端闸口(任意目录)
$env:AI_CLIP_BILLING_URL="http://127.0.0.1:8787"; $env:AI_LIVE_CLIP_WORKDIR="$PWD"
python client/payment_gate.py probe --video E:\rec\0710.ts        # -> 402, 账单落 .pay_cache/
# MOCK 环境用 dev 端点模拟支付拿到 proof(生产环境这步由 alipay-bot 完成):
curl -X POST "http://127.0.0.1:8787/api/v1/dev/mock-pay?out_trade_no=<订单号>"
python client/payment_gate.py complete --proof <proof>            # -> 200 已授权, 拿到 license token
```

生产环境的 `pay` / `complete` 会调用买家侧官方 CLI `alipay-bot`
(`402-buyer-pay` / `402-query-payment-status`); 本脚本只做编排, 不自行实现付款或签名。

自动化冒烟脚本见 `smoke_test.py`(9 项: 402→支付→验付→幂等重试→防重放→按量新订单
→持久化→**仅带凭证不回传订单号仍能放行**)。

## 2. 上生产的前置(只有你能做)

> ✅ **已澄清(2026-09-29)**: 官方明确**个人开发者可用手机号验证完成 SkillPay 入驻,
> 无需企业认证/营业执照**。资质不是硬闸门。真正的门槛是「开通收款产品」与「服务注册」。

完整 9 步清单(含入口 URL、可直接复制的提示词、验收表)见
**[docs/payskill-onboarding.md](../docs/payskill-onboarding.md)**, 按官方顺序:

1. SkillHub 个人入驻(手机号验证)
2. **技术集成与沙箱测试**(在产品开通**之前**做, 官方 Skill 自动配沙箱, 沙箱 service_id = `api_mock_service_id`)
3. 开通收款产品(aipay.alipay.com/products/machine-pay → 一站式接入)
4. 注册服务 → 拿 `API_` 开头真实 serviceId(登记地址须公网 HTTPS, 单价与服务端一致)
5. 密钥配置 + 部署(腾讯云轻量服务器 + Caddy)
6. 上线自测(买家账号 ≠ 卖家, 金额 0.01 元, 回填交易号)
7. 改造 Pay Skill
8. 发布付费 Skill(三处价格一致)
9. 上架后买家验证

前置依赖: Node.js(官方集成/买家支付都是 npm 包)、卖家支付宝账号、**另一个**买家支付宝账号。

安全红线: 私钥只存服务端环境变量/KMS; 客户端 payment_gate.py 不含任何密钥、不自行签名;
后端源码不随技能包分发; SQLite 订单库必须放持久盘(容器重建会丢)。

## 3. 部署方案(无服务器, 二选一)

| 维度 | A. 轻量云服务器(推荐) | B. Serverless 云函数 |
|---|---|---|
| 成本 | 腾讯云/阿里云 2C2G 轻量 ≈ ¥50–100/月 | 按量, 月调用 10 万次内基本免费 |
| 优点 | 完整 Linux, systemd/Docker 直跑, SQLite 够用, 以后可复用 | 零运维, 弹性 |
| 缺点 | 要自己管补丁/HTTPS | 402 自定义 Header 的 HTTP 触发器配置繁琐; SQLite 需换挂载盘/云库; 调试痛苦 |
| 结论 | **选 A**: 计费服务很轻, 一台最便宜的轻量云足够, Caddy 一行配 HTTPS | 量真起来再迁 |

最小部署(方案 A): 装 Python3.12 + `pip install -r requirements.txt` → systemd 跑 `uvicorn app:app --port 8787` → Caddy 反代 443 并自动签证书 → 客户端 `AI_CLIP_BILLING_URL` 指向 `https://你的域名`。

## 4. 定价建议

切片流水线跑在**用户本机**, 你的边际成本≈0(只有服务器固定成本)。定价就是纯利润 − 平台抽成(20–30%)。
建议: `PRICE_YUAN=1.00`(¥1/次, 走量) 或按视频时长分档(>1h 收 ¥2)。**必须与 SkillHub 发布表单的定价字段一致**。

## 5. 切换「付费技能」发布的操作清单

1. 服务端生产部署 + 沙箱全链路通过。
2. 把 `client/payment_gate.py` 拷入技能包 `scripts/`(客户不持有密钥, 可随包分发)。
3. SKILL.md 合入第 6 节的 PAYSKILL 契约章节, 并在执行流程第 1 步前加「付费闸口」步骤。
4. `pack_zip.py` 重打包 → SkillHub 发布表单计费模式选「按量付费」, 定价与 `PRICE_YUAN` 一致 → 提交。
5. 保留免费版: 同 slug 无法双版本, 上线付费即替换; 建议先用免费版攒够安装量再切。

## 6. SKILL.md 契约章节草稿(切付费时粘贴用)

```markdown
## 支付宝付费改造契约(PAYSKILL)

本技能为按量付费技能, 单次调用 ¥1.00。调用遵循支付宝 AI按量付费(A2M/402)协议,
付费闸口位于 `scripts/payment_gate.py`(编排官方 `alipay-bot`, 不含任何密钥),
云端计费服务地址由 `AI_CLIP_BILLING_URL` 配置。

三段式调用(与官方 probe/pay/complete 一致):

```bash
python scripts/payment_gate.py probe    --video <原视频路径>     # 未付款 -> 402, 账单落 .pay_cache/
python scripts/payment_gate.py pay                               # 交给官方支付能力, 等用户付款
python scripts/payment_gate.py complete --out-trade-no <订单号>   # 复用原订单取得授权, 不重新下单
```

- 402账单下发: 未持有效凭证时, 计费服务返回 `HTTP 402 Payment Required`,
  `Payment-Needed` 头携带 Base64URL 账单(out_trade_no/amount/resource_id/pay_before, RSA2 签名)。
  收到 402 即暂停交付, 将账单交给支付宝支付能力(alipay-payment-skill)完成支付, 不得跳过。
- 携带凭证重试: 支付成功后携带 `Payment-Proof` 重试同一资源请求(订单号不变, 不重新下单)。
- 验付调用: 服务端调 `alipay.aipay.agent.payment.verify` 验签, 并核对 active/金额/订单号/资源一致,
  任一不符返回 402 重新支付, 结果不明先查原订单, 不得要求重复付款。
- 履约确认: 资源生成后先调 `alipay.aipay.agent.fulfillment.confirm`, 确认成功才交付;
  失败返回 502, 可用同一 Payment-Proof 重试(不重复扣费)。
- 订单持久化与幂等: 订单/凭证哈希落库, out_trade_no 唯一, 同一 Payment-Proof 仅消费一次(防重放),
  同凭证重复重试返回同一授权, 不重复扣费。

本技能不自行实现付款或签名; 后端计费服务独立部署, 不包含在技能包内。
```
