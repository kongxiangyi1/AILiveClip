# 付费路线 · 硬前置执行清单

依据：
- SkillHub 官方教程 [个人接入 · 支付宝付费](https://skillhub.cn/tutorials#alipay-pay)
- 支付宝 [AI 按量付费（Machine Pay / A2M）接入指南](https://aipay.alipay.com/docs/ai-receive/MACHINE_PAY.html)

代码侧（`payserver/`）已就绪，**MOCK 模式 9/9 全链路通过**（`python smoke_test.py`）。
下面是只有你能做的部分，按官方顺序排列，**不要跳步**。

> ⚠️ 纠正上一轮的错误结论：官方明确**个人开发者可通过手机号验证完成 SkillPay 入驻，
> 无需企业认证 / 营业执照**。资质不再是硬闸门，真正的门槛是「开通收款产品」和「服务注册」。

---

## 0. 准备项（先凑齐，否则中途会卡）

| 项 | 要求 | 状态 |
|---|---|---|
| SkillHub 账号 | 已注册并登录 | ☐ |
| 卖家支付宝账号 | 用于开通收款产品 | ☐ |
| 买家支付宝账号 | **必须与卖家不同**，真实付款测试用 | ☐ |
| 业务 API | 已有 → `payserver/app.py`（`POST /api/v1/resource`） | ✅ |
| 本地开发环境 | **Node.js（含 npm）** + 支持加载 Skill 的 AI 编程助手 | ☐ |
| 部署环境 | 公网 HTTPS 地址（见第 5 步） | ☐ |

Node.js 是硬要求——官方集成和买家支付都是 npm 包。检查：`node -v` / `npm -v`。

---

## 1. 入驻 SkillPay（手机号验证）

1. 登录 SkillHub → 商户中心 → 「SkillPay 个人入驻」
2. 输入手机号 + 验证码，提交

**完成标志**：SkillHub 显示「已入驻」。

---

## 2. 技术集成与沙箱测试（在产品开通**之前**做）

这一步不用真实资金，官方 Skill 会自动配好沙箱。

```bash
cd E:/WorkBuddyCliping1/ai-live-clip/payserver
npx -y @alipay/alipay-aipay@latest install
```

然后把下面这段提示词发给 AI 编程助手（业务信息已按本项目填好，直接复制）：

```
请加载 alipay-aipay，为本项目的业务 API 接入支付宝 AI 按量付费，并在沙箱环境中完成测试。

业务信息：
- 接口用途：为「直播切片工具」发放一次流水线执行授权（license token），
  授权后客户端在本地跑 FunASR 转写 → 语义分段 → FFmpeg 切割
- 请求方式及路径：POST /api/v1/resource
- 请求体：{"resource_id": "liveclip_<sha1前16位>"}；Content-Type: application/json
- 必要请求头：付款后追加 Payment-Proof: <base64 编码凭证>
- 单次价格：1.00 元

要求：
1. 保留接口原有业务逻辑和成功响应（200 返回 license token）。
2. 验证 402 账单、Payment-Proof 验证、资源交付、履约回执和同一订单重试。
3. 输出测试结果、代码变更、启动方式、环境变量模板，以及上线前需要替换的生产配置；不要求我提供生产私钥。
4. 本轮不发起真实付款；缺少信息时先向我确认。
```

**验收**：Agent 明确给出 402 触发、收银下单、凭证重试、验付履约四项结果全成功。

**注意**：沙箱 `service_id` 是 `api_mock_service_id`，**上线前必须替换**为第 4 步的真实 serviceId。

---

## 3. 开通收款产品

- 入口：https://aipay.alipay.com/products/machine-pay → 「一站式接入」
- 用**卖家**支付宝账号扫码登录 → 产品选「AI 按量付费」→ 身份验证 + 签约
- 经营类目与资质按实际填写 → 等审核通过

> 拿到应用 ID ≠ 收款能力已生效。必须确认**产品已开通、应用可用**再做真实交易。

---

## 4. 注册服务（拿 `API_` 开头的 serviceId）

入口：https://aipay.alipay.com/open-flow/console → 「服务管理」

按下面填（**服务地址填错会导致付款失败**）：

| 字段 | 填写值 |
|---|---|
| 服务名称 | 直播切片工具·按量付费 |
| 服务类型 | 与实际业务接口一致 |
| 服务地址 | `https://<你的域名>/api/v1/resource` —— 买家实际请求的完整 URL，须公网 HTTPS 可达 |
| 服务单价 | **1.00**（0.01～50 元/次，最多两位小数，**须与服务端账单一字不差**） |
| 服务描述 | 直播回放长视频按话题自动粗切为可独立传播的短视频片段，发一次执行授权 |
| 请求示例 | `POST /api/v1/resource`，body `{"resource_id":"liveclip_xxxxxxxxxxxxxxxx"}`，头 `Content-Type: application/json` |

**地址未就绪时**：先做第 5 步部署，再回来注册；拿到 serviceId 后补进生产配置重新部署。
地址变更时，支付宝「服务管理」和 Skill 里都要同步改。占位地址不能用于付款验证。

---

## 5. 配置密钥并部署

### 5.1 密钥

用[支付宝开放平台密钥工具](https://opendocs.alipay.com/open/02kipk)生成密钥：

- 应用公钥 → 配到支付宝开放平台对应应用
- 取得支付宝公钥
- **应用私钥只存服务端**（环境变量 / KMS），不写入 Skill、日志或代码仓库

### 5.2 服务端配置

`cp payserver/config.env.example payserver/config.env`，填：

| 变量 | 来源 |
|---|---|
| `ALIPAY_APP_ID` | 支付宝开放平台生产应用 AppId |
| `ALIPAY_APP_PRIVATE_KEY` | 应用私钥（格式须与 SDK 配置一致） |
| `ALIPAY_PUBLIC_KEY` | 支付宝公钥 |
| `ALIPAY_SELLER_ID` | 卖家支付宝用户 ID（2088 开头） |
| `ALIPAY_SERVICE_ID` | 第 4 步的 `API_...` serviceId（**替换沙箱的 api_mock_service_id**） |
| `ALIPAY_GATEWAY` | `https://openapi.alipay.com/gateway.do` |
| `ALIPAY_MOCK` | **必须改为 0** |

### 5.3 部署

没有服务器就用腾讯云轻量应用服务器（Lighthouse），官方推荐：
[活动套餐](https://cloud.tencent.com/act/pro/lhsale) · [新手指引](https://cloud.tencent.com/document/product/1207/47147)

```
装 Python 3.12 → pip install -r payserver/requirements.txt
→ systemd 跑 uvicorn app:app --port 8787
→ Caddy 反代 443 自动签证书
```

> **持久化警告**：容器本地文件可能随重建丢失。SQLite 库（`payserver/data/orders.db`）
> 必须放持久盘，量起来后换云数据库——丢订单库等于丢履约记录。

### 5.4 验证部署（只检查 402，不发起付款）

```bash
curl -i --connect-timeout 10 --max-time 30 'https://你的服务域名/api/v1/resource' -X POST \
  -H 'Content-Type: application/json' -d '{"resource_id":"liveclip_test"}'
```

应看到 **HTTP 402** + `Payment-Needed` 响应头。根路径 `/` 的 404 不能说明业务端点可用。

---

## 6. 上线自测（**会产生真实扣款**）

开始前确认：买家账号 ≠ 卖家账号；金额 0.01 元（与支付宝注册价一致）。

```bash
npx -y @alipay/agent-payment@latest install
```

```
请加载 alipay-payment-skill，对已部署的业务接口进行上线自测。

业务信息：
- 接口用途：为「直播切片工具」发放一次流水线执行授权
- 请求方式及完整 URL：POST https://<你的域名>/api/v1/resource
- 请求体：{"resource_id": "liveclip_<sha1前16位>"}；Content-Type: application/json
- 支付宝服务注册价格：1.00 元（自测时改 0.01）

要求：
1. 发起原始请求，核对 HTTP 402 和账单金额，并按官方流程生成支付入口；展示后等待我付款。
2. 我付款后，复用原请求和原订单获取资源，不重新下单。
3. 核对业务结果和商家履约记录；无法读取服务端日志时，列出需要我核对的项目。
4. 状态不明时先查询原订单，不重复付款或追加回执；缺少信息时先向我确认。
```

**自测验收表**（全过才算完）：

| 检查项 | 通过标准 |
|---|---|
| 产品与应用 | 收款产品已开通，生产应用可用 |
| 配置 | 密钥、网关、serviceId 均为生产值 |
| 地址 | 公网业务端点返回 402，登记地址与实际请求一致 |
| 价格 | 支付宝登记价 = 服务端 402 账单金额 |
| 买家账号 | 与卖家不同 |
| 交付与履约 | 付款成功、结果正确、商家履约已确认 |
| 订单恢复 | 同一订单重试不重复扣费，服务端履约幂等 |

最后到[支付宝商家中心 - 我卖出的交易](https://b.alipay.com/page/mbillexprod/trade/order/sold)核对交易，
按 AI 付站点线上验证页面要求**回填交易号**。

---

## 7. 改造 Pay Skill（把付费流程接进技能）

> ✅ **已完成（2026-09-29）**：闸口已拷入技能包 `scripts/payment_gate.py`，
> SKILL.md 已加入「步骤 0 付费闸口」+「付费协议(A2M/402)」章节。
> 下面保留原始提示词仅作存档；重新执行无需再做。

服务端已跑通后，把 `payserver/client/payment_gate.py` 拷进技能包 `scripts/`，
并让 Agent 合入契约章节：

```
请先检查当前项目是否已有业务 Skill（SKILL.md 在 C:/Users/KONGXY/.workbuddy/skills/直播切片工具），
保留原有业务能力、触发条件和输出，为它接入已经部署的收费接口。

业务信息：
- Skill 名称：ai-live-clip
- 能力与触发词：直播回放/录播长视频按话题自动粗切为短视频片段；"直播切片""直播回放剪辑""带货视频粗剪"
- 请求方式及完整资源 URL：POST https://<你的域名>/api/v1/resource
- 请求体：{"resource_id": "liveclip_<sha1前16位>"}；Content-Type: application/json
- 单次价格：1.00 元（与支付宝服务登记一致）
- 成功响应及展示字段：{"ok":true,"token":"PASS_<out_trade_no>","out_trade_no":"...","amount":"1.00"}

要求：
1. 保留现有 Skill 的触发条件、输入和结果展示，只补充收费流程；写清输入、价格、依赖和调用方法。
2. 按已安装的官方文档调用支付能力（scripts/payment_gate.py 负责编排 probe/pay/complete），
   不自行实现付款或签名；支付后复用同一订单，状态不明时先查单，成功路径不追加独立回执。
3. 提供安装、运行、本地加载和打包说明；排除密钥、.env、状态文件和 node_modules。
4. 用模拟响应测试 402、支付处理中、成功、失败和同订单重试；再加载本地 Skill 验证触发、
   参数传递和 402 处理，停在付款确认前。
5. 列出仍需补充的真实配置；缺少信息时先向我确认，未经确认不发起真实付款。
```

**交付目录约定**：

```
ai-live-clip/
├── SKILL.md                      # 含「支付宝付费改造契约」章节
├── scripts/
│   ├── payment_gate.py           # 付费闸口（编排 alipay-bot，不含任何密钥）
│   └── ...                       # 原有流水线脚本
└── references/
```

> 后端源码（`app.py` / `alipay_client.py` / `orders.py`）**独立部署，不放入 Skill 包**。

---

## 8. 发布

0. 打包（已支持双版本，在 `ai-live-clip/` 目录执行）：
   ```
   python pack_zip.py --variant free    # -> dist/ai-live-clip.zip     (免费版, 剔除付费章节与闸口)
   python pack_zip.py --variant pay     # -> dist/ai-live-clip-pay.zip (付费版, 完整)
   ```
   付费章节用 `<!-- PAYSKILL_BEGIN/END -->` 标记，免费版自动整段剥离；
   打包脚本会二次确认免费包内无 `payment_gate.py`、无付费章节残留。

1. SkillHub → 「发布 Skill」→ 选「**发布付费 Skill**」
2. 上传 ZIP：**最多 200 个文件，总大小 ≤ 10 MB**（用 `ai-live-clip-pay.zip`）
3. Slug 用小写字母、数字和连字符（`ai-live-clip`，提交后不可改）
4. 「完善计费」填**单次调用价格 1.00**

> **三处价格必须一致**：支付宝登记价 = 服务端 `PRICE_YUAN` = SkillHub 展示价。

**提交前自查**（平台会静态扫描，文案声明不能替代真实可用服务）：

| 自查项 | 本项目实现 |
|---|---|
| 402 账单 | `app.py` 无凭证返回 402 + `Payment-Needed` 头 |
| 凭证重试 | `payment_gate.py complete` 复用原订单号重试 |
| 服务端验付 | `alipay.aipay.agent.payment.verify` + active/金额/订单号/资源逐项核对 |
| 商家履约 | **资源生成 → confirm 成功 → 才交付**（非异步后置）；失败返 502，同一凭证重试不重复扣费 |
| 成功回执 | 交付响应带 `Payment-Validation` 头（Base64URL: trade_no/out_trade_no/validated/resource_id） |
| 订单与幂等 | SQLite：out_trade_no 主键、trade_no/proof_hash UNIQUE 防重放、`prepare_fulfillment` 原子化 |
| 生产旁路防护 | 生产网关 + MOCK / 占位 serviceId / 缺私钥 → 启动期硬失败拒绝服务 |

包内不得有：密钥、`.env`、`node_modules`、`.pay_cache/` 状态文件。

---

## 9. 上架后买家验证（强烈建议，别省）

沙箱和自测覆盖不了上架版本的安装、触发、生产配置和真实支付体验。
从 SkillHub **安装已上架的版本**，用买家账号真跑一次：

1. 按详情页指引安装，确认官方支付依赖可用
2. 用真实用户口吻调用，确认触发正确、参数完整
3. 核对详情页展示价、付款前提示、实际账单金额三者一致
4. 用买家支付宝完成付款
5. 确认买家收到切片授权、卖家查到收款、服务端履约已确认

---

## 参考链接

| 内容 | 链接 |
|---|---|
| SkillHub 个人接入教程 | https://skillhub.cn/tutorials#alipay-pay |
| 支付宝 AI 按量付费接入指南 | https://aipay.alipay.com/docs/ai-receive/MACHINE_PAY.html |
| 收款产品开通 | https://aipay.alipay.com/products/machine-pay |
| 服务管理 / 注册地址 | https://aipay.alipay.com/open-flow/console |
| 密钥工具指南 | https://opendocs.alipay.com/open/02kipk |
| 验付接口文档 | https://ideservice.alipay.com/cms/site/0j7uot |
| 履约确认接口文档 | https://ideservice.alipay.com/cms/site/0j7sw0 |
| service_id 获取 FAQ | https://ideservice.alipay.com/cms/site/0j7uos |
| 官方 402 示例代码 | https://github.com/alipay/ai/tree/main/code_example/aipay-402-example |
| 商家交易核对 | https://b.alipay.com/page/mbillexprod/trade/order/sold |
| 腾讯云轻量服务器 | https://cloud.tencent.com/act/pro/lhsale |

## 协议要点备忘（实现时已对齐）

- **账单签名**：RSA2，8 个字段（amount / currency / goods_name / out_trade_no /
  pay_before / resource_id / seller_id / service_id）按 key 字典序拼 `k=v&k=v` 后加签，
  结果放 `protocol.seller_signature`。**本地加签，不请求支付宝**。
- **Payment-Proof 结构**：`{protocol:{payment_proof, trade_no}, method:{client_session}}`
  —— **不含 out_trade_no**，所以服务端必须能按 trade_no / 验付返回值反查订单（已实现）。
- **验付不能只看接口成功**：必须核对 `active=true`、金额、out_trade_no、resource_id 四项，
  任一不符返回 402 重新支付。
- **履约时机**：返回资源之后**异步**调用，不是之前。
- **防重放**：`pay_before` 有效期 + RSA2 签名 + trade_no 不重复履约。
