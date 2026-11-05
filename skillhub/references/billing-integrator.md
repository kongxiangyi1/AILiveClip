# 付费服务端接入手册（仅开发者）

> **谁需要读**：要改动收款链路的人——部署/迁移计费服务端、改价、换 `service_id`、
> 排查 402 协议字段与履约确认。正常使用者**不需要**读这份，只走 SKILL.md《付费与授权》的四条命令。
>
> **范围声明**：计费服务端源码（FastAPI + uvicorn，独立维护于 `AILiveClip` 仓库的 `payserver/`）
> 与私钥签发工具都**不在技能包内**（私钥不能随包分发）。本包只含客户端
> `payment_gate.py` / `licensing.py` 与随包公钥 `config.json`。
> 以下内容的价值在于排障时判断"问题出在客户端还是服务端"。

---

## 沙箱服务端(支付宝 AI 付 · A2M/402)—— 本技能采用的收款方式

### 为什么必须有服务端

> ⚠️ **更正记录(2026-10-29)**: 早前曾依据技能详情页的营销描述判断「平台代收、无需服务端」,
> 该结论**已被官方手册推翻**。SkillHub 对每个付费技能都挂着免责声明:
> 「SkillHub 仅提供展示、投诉处理及其他平台服务, **不作为该笔交易的收款方**」——
> 平台不代收, 钱直接进开发者账户。官方上架手册的 Pay Skill vs 普通 Skill 对比表写明:
> **Pay Skill 必须有服务端**(出账 + 验付 + 履约确认)。

但"必须有服务端" ≠ 自己买服务器。官方提供**应用沙箱**方案: 零依赖 / 零服务器 / 零域名,
部署到 WorkBuddy 应用沙箱后由平台分配 `https://随机名.app.workbuddy.host`, 自带证书、免备案。

| 环节 | 谁做 |
|---|---|
| 预下单 `alipay.aipay.agent.payment.precreate` | **本沙箱服务端** |
| 402 账单下发(`amtPaymentLink` 预下单号 + `Payment-Needed` Base64URL 账单) | **本沙箱服务端** |
| 拉起支付宝收银台 / 用户付款 | 用户本人(Agent 只编排, 不代付) |
| 验付 `alipay.aipay.agent.payment.verify` | **本沙箱服务端** |
| 签发买断授权码(履约) | **本沙箱服务端** |
| 履约确认 `alipay.aipay.agent.fulfillment.confirm` | **本沙箱服务端**(确认成功才交付) |
| 之后每次 probe 验签放行 | **客户端本地**(不联网、不计费) |

### 买断的落地机制(关键)

官方体系只有按次计费, **没有"买断"这个概念**。买断是这样实现的:

```
第 1 次: probe(无授权) -> 服务端 402 出账 -> pay 付款 -> complete 取回授权码 -> 落盘
第 2 次起: probe 见本地授权 -> 本地 RSA 验签通过 -> 直接放行, 零网络请求、零费用
```

因此**有授权时绝不能再请求服务端**(否则会被重复计费)——客户端 `probe` 已把"本地验签"
放在最前面, 命中即返回。换机/重装后凭旧授权码可走服务端「恢复」分支拿回, 同样不重复扣费。

### 当前线上服务（开发者侧，只读）

> ⚠️ **本节只对本技能的开发者有意义**，买家无需阅读、也无需执行任何一步。
> 服务端源码（FastAPI + uvicorn）**不在本技能包内**，独立维护于 `AILiveClip` 仓库的 `payserver/`。

| 项 | 实际值 / 说明 |
|---|---|
| 对外端点 | `GET/POST /api/license`（402 出账 / 带 `Payment-Proof` 取货）、`GET /api/license/status`、`GET /api/orders`、`GET /health` |
| 服务注册 | `service_id = API_AAD872169A4E45FE`，支付宝后台**审核已通过**，登记价 19.90 |
| 配置方式 | 服务端 `.env`（`ALIPAY_APP_ID` / `ALIPAY_SERVICE_ID` / `ALIPAY_SELLER_ID` 等）+ `keys/` 私钥 |
| 部署 | WorkBuddy 应用沙箱，平台分配 `https://<随机名>.app.workbuddy.host`，访问须带 `?sharecode=` |
| 订单 | SQLite 持久化，`out_trade_no` 唯一，同一凭证幂等履约 |
| 授权码 | `keys/license_private_key.pem`（PEM）签发 `LIVECLIP1.*`，客户端用 `config.json` 公钥离线验签 |

> 🔴 **两套私钥不可混用**：支付宝应用私钥用于支付签名；`license_private_key` 用于签发授权码，
> 其对应公钥已写进包内 `config.json`。混用会导致授权码在客户端验签失败。
>
> ⛔ **改配置只能改服务端 `.env` 后重新部署**：服务端**没有任何 HTTP 管理端点、也没有管理令牌**，
> 实际路由清单以线上 `/openapi.json` 为准。早期草稿里的 `curl` 注入密钥、管理令牌与沙箱固定服务号
> 均已废弃，**不要照做**（照做必然 404）。

### 三步走协议(官方, 与《AI按量付费 产品接入指南》对齐)

```
第0步(服务端内部) alipay.aipay.agent.payment.precreate -> amt_pre_pay_id

POST /api/license                      无凭证 -> 402
                                       amtPaymentLink: <amt_pre_pay_id>        ← 产品接入指南口径
                                       Payment-Needed: b64url({"protocol":{...},"method":{...}})
                                                                                ← SkillHub 口径
POST /api/license + Payment-Proof 头   验付 -> 签发授权码 -> 履约确认 -> 200 + license_key
POST /api/license + body.license_key   凭旧码恢复(换机/重装), 不重新出账
GET  /api/license/status?out_trade_no= 排障: 只回订单状态, 不回授权码
```

`Payment-Proof` = `b64url({"protocol":{"payment_proof":..,"trade_no":..},"method":{"client_session":..}})`。
★ 协议硬约束: **带凭证重试时 Body 必须与首次完全一致**, 否则服务端映射不回原订单
(客户端已把首次 body 存进 `.pay_cache/state.json` 并原样重发)。

### 三段式调用(与官方 probe/pay/complete 一致)

```bash
python "<SCRIPT_DIR>/payment_gate.py" license                          # 查本机买断授权(不发网络请求)
python "<SCRIPT_DIR>/payment_gate.py" probe [--video <路径>]            # 已买断 -> 本地验签放行(0); 未买断 -> 402 出账
python "<SCRIPT_DIR>/payment_gate.py" pay                              # 交给官方支付能力, 展示支付入口, 等用户付款
python "<SCRIPT_DIR>/payment_gate.py" complete                         # 付款后带凭证取回授权码并落盘(不重新下单)
python "<SCRIPT_DIR>/payment_gate.py" complete --out-trade-no <订单号>   # 先查原单再取货(凭证由 CLI 输出自动捕获)
python "<SCRIPT_DIR>/payment_gate.py" complete --proof <凭证>           # CLI 未回传凭证时手动指定
python "<SCRIPT_DIR>/payment_gate.py" status  --out-trade-no <订单号>    # 排障: 查订单状态(不重新付款)
python "<SCRIPT_DIR>/payment_gate.py" activate <授权码>                 # 离线/人工通道激活
```

**首次之后不用再付**: 首次付款成功后, 服务端签发的授权码持久化到 `~/.ai-live-clip/license.json`。
之后每次 `probe` **先做本地 RSA 验签**, 命中即放行 —— 不联网、不出账,
**换视频、换目录、换调用次数都有效**。服务端挂掉也不影响已买断用户。

### 五条契约(⛔ 逐条不可违反)

| # | 契约 | 行为 |
|---|---|---|
| 0 | **预下单** | 服务端先调 `alipay.aipay.agent.payment.precreate` 取得 `amt_pre_pay_id`，再出 402。⛔ 少了这步支付宝侧没有这笔单，用户付款必然失败 |
| 1 | **402 账单下发** | 未持有效凭证时计费服务返回 `HTTP 402` + `amtPaymentLink`(预下单号) + `Payment-Needed` 头(out_trade_no/amount/resource_id/pay_before, RSA2 签名)。收到 402 **即暂停交付**, 把账单交给支付宝支付能力完成支付, ⛔ 不得跳过 |
| 2 | **携带凭证重试** | 支付成功后携带 `Payment-Proof` **重试同一资源请求**(订单号不变, ⛔ 不重新下单) |
| 3 | **验付调用** | 服务端调 `alipay.aipay.agent.payment.verify` 验签, 核对 active/金额/订单号/资源一致; 任一不符返回 402 重新支付。**结果不明先查原订单, ⛔ 不要求重复付款** |
| 4 | **履约确认** | 资源生成后先调 `alipay.aipay.agent.fulfillment.confirm`, **确认成功才交付**; 失败返回 502, 可用同一 Payment-Proof 重试(不重复扣费) |
| 5 | **订单持久化与幂等** | 订单/凭证哈希落库, out_trade_no 唯一, 同一 Payment-Proof 仅消费一次(防重放), 同凭证重复重试返回同一授权 |

### 官方协议校准(2026-10-31 · SkillHub 教程 + 支付宝《AI按量付费 产品接入指南》2026-10-21)

> 两份官方文档口径互补, 本技能**已按两者交集实现**:
> - 《产品接入指南》(支付宝): 定义服务端三步接口 `precreate` → `payment.verify` → `fulfillment.confirm`,
>   402 响应头为 **`amtPaymentLink`**(承载预下单号 `amt_pre_pay_id`)
> - 《SkillHub 教程》: 定义买家侧交互, 402 响应头为 **`Payment-Needed`**(Base64URL 完整账单)
>
> ⚠️ 两者不冲突: 本服务端**同时下发两个头**, 账单体内也内嵌 `amt_pre_pay_id`, 买家侧 CLI 任一取法都能拿到。

**服务端五步**(缺一不可, 顺序不能颠倒):

| # | 步骤 | 接口 / 动作 |
|---|---|---|
| 0 | **预下单** | `alipay.aipay.agent.payment.precreate`(out_trade_no / service_id / goods_name / amount / pay_before) → 取得 `amt_pre_pay_id` |
| 1 | **402 出账** | 返回 `HTTP 402` + `amtPaymentLink: <amt_pre_pay_id>` + `Payment-Needed: b64url({protocol,method})` |
| 2 | **验付** | 读 `Payment-Proof` → `alipay.aipay.agent.payment.verify`(传 payment_proof / trade_no / client_session) |
| 3 | **业务校验** | ★ 只看接口 `code=10000` 不够, 必须核对 `active=true`、`amount`、`out_trade_no`、`resource_id`, 且 `trade_no` 未被重复履约 |
| 4 | **履约确认** | `alipay.aipay.agent.fulfillment.confirm` 成功才交付资源(本技能交付的是买断授权码) |

| 字段 | 位置 | 说明 |
|---|---|---|
| `402` | 状态码 | 需付费; 返回时须同时下发账单 |
| `amtPaymentLink` | **响应头** | ★ 产品接入指南口径, 值 = 预下单号 `amt_pre_pay_id` |
| `Payment-Needed` | **响应头** | ★ SkillHub 口径, 值 = b64url 完整账单(本技能其中也含 `amt_pre_pay_id`) |
| `Payment-Proof` | 请求头 | 支付凭证 |
| `Payment-Validation` | **响应头** | ★ 验付通过后由服务端返回, 客户端据此判定"已履约" |
| `Idempotency-Key` | 请求头 | 调用方生成的幂等键, 防重复扣款/重复履约(本技能已落库) |
| `payment-order-id` | 响应体/请求头 | 订单号, 贯穿全程(本技能响应体同时给 `payment_order_id` / `out_trade_no` 兼容旧客户端) |
| `pay-url` | 响应体 | 支付宝收银台地址 |
| `ack` | 响应体 | 履约回执 `{status:"FULFILLED", result:...}` |

**★ 放行判据(缺一不可)**:
```
HTTP 200  +  响应头 Payment-Validation  +  body.fulfillment_confirmed == true
```
只看到 200 不算成功 —— 客户端已按此判据实现, 缺头会返回 502 并不交付授权码。

**错误码表**(服务端已实现, 响应体 `code` 字段):

| code | HTTP | 含义 | 处理 |
|---|---|---|---|
| `OK` | 200 | 验付通过并履约完成 | 读 `ack` 与 `license_key` |
| `PAYMENT_REQUIRED` | 402 | 未付费 | 带 `Payment-Proof` 重试 |
| `PROOF_INVALID` | 402 | 凭证无效/已使用 | 重新支付取新凭证 |
| `AMOUNT_MISMATCH` | 402 | 金额与账单不符 | 核对后重新支付 |
| `PAYMENT_EXPIRED` | 402 | 账单超时 | 重新 probe 下单 |
| `INVALID_PARAM` | 400 | 参数缺失/非法 | 修正入参 |
| `ORDER_NOT_FOUND` | 404 | 订单不存在 | 重新 probe |
| `ALIPAY_GATEWAY_ERROR` | 502 | 支付宝网关失败 | 稍后重试, 不重复扣款 |

**买家侧 CLI 确切参数**(⛔ 不要臆造参数名):
```bash
alipay-bot 402-buyer-pay --session-id <会话ID> --file <probe 落盘的 payment-needed 文件> \
  --resource-url <资源URL> --method <GET|POST> --intent-summary "原始请求：<摘要>"
alipay-bot 402-query-payment-status --out-shake-no <交易号> --resource-url <URL> --method <方法>
```
票据**必须原样落盘**(Base64URL 原始串, 不解码、不改写), 否则 `alipay-bot` 读不到账单。
`--body` / `--headers` **官方 CLI 并无此参数**, 本技能默认不下传(需时可用环境变量开启)。

### 红线

- ⛔ **不自行实现付款或签名** —— 一律调用官方 `alipay-bot` / 支付宝官方能力
- ⛔ **私钥只在服务端**(环境变量/KMS), 不进 Skill 包、日志、代码仓库
- ⛔ **不绕过闸口** —— 未拿到授权前不进入阶段 1 转写(转写耗时 12-25 分钟, 先跑再收费等于漏收)
- ★ **三处价格必须一致**: 支付宝服务登记价 = 服务端 402 账单金额 = SkillHub 展示价 = **19.90 元**
  (两位小数; 客户端常量 `AI_CLIP_PRICE_CNY` 默认 19.90, 与服务端账单不符时脚本会打印告警)
- ★ **平台展示口径**: SkillHub 详情页统一标注「按次收费」并附免责声明, 平台表单**没有"买断"选项** ——
  **买断是本技能服务端的实现**(见上条), 平台侧仍按"19.90 元/次"展示。
  因此发布时: 表单价格填 **19.90**; 描述与案例里务必写明「**买断制 · 付一次永久有效**」,
  否则用户看到"按次收费"会产生预期差 → 投诉, 或审核误判"价格不符"
- ★ **计费口径: 买断** —— 付一次即永久有效。**resource_id 是固定的买断资源标识, 与具体视频无关**,
  因此换任何视频、调用多少次都**不再产生账单**; 只有**首次**(本机无授权时)才需要付款
- ⛔ **卖家侧无权创建"付款链接"给用户直接点** —— 付款必须由买家侧发起(`alipay-bot 402-buyer-pay`)。
  注意区分: 服务端调 `precreate` 拿的是**预下单号**(官方第一步, 必须做),
  不是收银台链接。若 `precreate` 返回 `insufficient-isv-permissions`,
  原因是**「AI按量付费」产品未开通 / 服务未注册 / serviceId 未生效**, 不是接口不该调 ——
  去支付宝开放平台核对产品开通状态与 `service_id` 的注册绑定。
  其它预下单错误的定位方法见下节《预下单失败排障》。
- ⛔ **买家账号不能等于商家账号** —— 否则支付宝拒绝支付(`买家和卖家不能相同`)。
  自测付款时**必须换一个支付宝账号**, 不能用你自己的商家号付给自己
- ⛔ **不向用户暴露支付链接原文、二维码原文或交易号** —— 只展示金额与"请在弹出的收银台完成支付"

### 接入现状（开发者记录）

> 买家可跳过本节。下表用于排障时确认「服务端是否具备收款条件」。
> ⛔ **不要再按沙箱接入流程排查**——产品开通、服务注册、密钥配置与线上部署均已完成。

| 项 | 状态 |
|---|---|
| 开通「AI 按量付费」产品 | ✅ 已完成 |
| 注册服务 / 拿 serviceId | ✅ 已完成（`API_AAD872169A4E45FE`，后台审核已通过，登记价 19.90） |
| 生产密钥配置与线上部署 | ✅ 已完成（HTTPS 可访问，402 出账正常） |
| 预下单 `precreate` + `amtPaymentLink` | 官方协议必需；服务端代码已实现，**需在服务端重新部署后生效**（见《预下单失败排障》） |
| 发布 SkillHub | ✅ 同一 slug 已上架（更新发布以 frontmatter `version` 为准） |
| 买家真实付款验收 | ⏳ 需买家本人开通 AI 支付钱包后完成 |

> ⛔ 沙箱阶段的资料（固定服务号、管理端点、注入密钥命令、接入待办清单）**不随本包分发**。
> 若从旧版文档、缓存或对话历史里读到，一律以本节与线上 `/openapi.json` 为准。

### 预下单失败排障（付款前先看这里）

官方 402 链路的第 0 步是 `alipay.aipay.agent.payment.precreate`；**没有真实 `amt_pre_pay_id`，
支付宝侧就没有这笔预订单，买家一定付不了款**。此时反复重试 `pay` 没有意义。

| 现象 | 含义 | 处理 |
|---|---|---|
| 402 响应头**无** `amtPaymentLink`，响应体含 `precreate_error` | 服务端预下单被支付宝拒绝 | 把 `out_trade_no` 与 `precreate_error` **原文**发给作者；不要改客户端、不要猜卖家配置 |
| 账单里预下单号以 `MOCKPRE` / `LOCALFALLBACK` 开头 | 本地模拟值，仅联调用 | 同上，这笔账单付不了款 |
| `probe` 打印「[服务端预下单失败] …」 | 客户端已把上面两类错误直接显示在终端 | 按错误原文定位，不要在下游找原因 |
| 预下单返回 `insufficient-isv-permissions` | 产品未开通 / 服务未注册 / serviceId 未生效 | 卖家本人到支付宝开放平台核对，买家侧无法处理 |
| 预下单返回 `sub_msg` 含卖家标识不匹配 | 服务注册绑定的卖家与账单字段不一致 | 以支付宝后台服务详情为准核对，⛔ 不要凭格式猜测收款 PID |

> 常见误判：把「付不了款」归因为「卖家还没开通产品」或「技能文档里的待办」。
> 产品开通与服务注册**已完成**；先看 `precreate_error` 原文，再谈改配置。
>
> 另一处容易混淆的是**买家侧 AI 支付钱包未开通**（`alipay-bot check-wallet` 返回「未开通」）：
> 这是买家侧问题，需买家本人扫码授权，卖家无法代开，与 `precreate_error` 是两回事。

### 买家侧依赖

```bash
npx -y @alipay/agent-payment@latest install    # 买家支付能力(alipay-bot)
```

> 首次使用若提示找不到 `alipay-bot`, 先执行上面这条安装命令再重试。
> 脚本只做编排, 不接触支付密钥, 也不自行实现支付。

### 客户端环境变量(一般不设, 排障时用)

| 变量 | 默认 | 用途 |
|---|---|---|
| `AI_CLIP_BILLING_URL` | 取 `config.json` 的 `service_url` | 沙箱 HTTPS 地址。环境变量优先于配置文件; 两者都空则退回本地授权模式 |
| `AI_CLIP_RESOURCE_PATH` | `/api/license` | 买断资源端点(须与服务端注册的 path 一致) |
| `AI_CLIP_PRICE_CNY` | `19.90` | 买断价; 与 402 账单金额不符时打印告警 |
| `AI_CLIP_HTTP_RETRIES` / `_SLEEP` | `2` / `3` | 网络故障与 5xx 的重试次数 / 间隔秒 |
| `AI_CLIP_OFFLINE_GRACE` | `1` | 已买断用户遇服务端故障是否离线放行(置 `0` 关闭) |
| `AI_CLIP_LICENSE_FILE` | `~/.ai-live-clip/license.json` | 授权文件路径(仅联调覆盖用) |
| `ALIPAY_BOT_CMD` / `_BODY_FLAG` / `_HEADERS_FLAG` | `alipay-bot` / `--body` / `--headers` | alipay-bot CLI 适配(参数名以本机安装的官方文档为准) |
