# 付费与授权(使用者向)

> 命令里的 `<SCRIPT_DIR>` 指本技能的 scripts/ 目录, 取法见 SKILL.md《前置环境》。

## 计费口径: 买断制 ¥19.90

- **付一次永久有效**, 不限视频数量、不限调用次数、不按次扣费
- 买断授权码(RSA 签名)落盘到 `~/.ai-live-clip/license.json` 之后, 所有校验都在**本机完成**:
  不联网、不请求服务端、不上传任何数据
- 换视频、换目录、换多少次调用都**不再产生账单**; 只有**首次**(本机无授权时)才需要付款

## 首次付费与后续直通

```
第 1 次: probe(无授权) → 402 出账 → pay 付款 → complete 取回授权码 → 落盘
第 2 次起: probe 见本地授权 → 本地 RSA 验签通过 → 直接放行, 零网络请求、零费用
```

```bash
python "<SCRIPT_DIR>/payment_gate.py" license        # 查本机买断授权(不发网络请求)
python "<SCRIPT_DIR>/payment_gate.py" probe          # 已买断 → 放行(0); 未买断 → 402 出账
python "<SCRIPT_DIR>/payment_gate.py" pay            # 交给官方支付能力, 收银台付款
python "<SCRIPT_DIR>/payment_gate.py" complete       # 付款后取回授权码并落盘(不重新下单)
python "<SCRIPT_DIR>/payment_gate.py" complete --out-trade-no <订单号>   # 先查原单再取货
python "<SCRIPT_DIR>/payment_gate.py" status  --out-trade-no <订单号>    # 排障: 查订单状态
python "<SCRIPT_DIR>/payment_gate.py" activate <授权码>                 # 离线/人工通道激活
```

**退出码判据**: `0` 放行 / `402` 待支付 / `2` 授权码无效 / `3` 闸口不可用 / `4` 验签失败或无法落盘 / `5` 已付款但取不到凭证。
⚠ shell 看到的退出码是 `code & 0xFF`, 所以 `402` 显示为 **146**; 判据以文档值(402)为准。

## 买家侧依赖

```bash
npx -y @alipay/agent-payment@latest install    # 买家支付能力(alipay-bot)
```

首次使用若提示找不到 `alipay-bot`, 先执行上面这条命令再重试。
`402-buyer-pay / 402-query-payment-status` 的确切参数以本机安装的官方 CLI 为准; 票据(Base64URL)必须**原样落盘**, 不解码、不改写。

## 授权码与校验

```
格式   LIVECLIP1.<payload_base64url>.<签名_base64url>
载荷   {"oid": 订单号, "amt": "19.90", "mid": 机器码或"*", "iat": 签发时间, "exp": ""或到期, "mode": "perpetual"}
```

- 校验在 `scripts/licensing.py`: 机器码匹配、过期判断、金额一致性、base64 规范化四道
- 无 `cryptography` 无法验签 → 明确报错提示 `pip install cryptography`, ⛔ 不静默放行

## 换机 / 重装

- 换机**不重复付费**: 凭旧授权码走服务端「恢复」分支取回; 通用码 `mid="*"` 直接在新机
  `python "<SCRIPT_DIR>/payment_gate.py" activate <授权码>`
- 授权码存在 `~/.ai-live-clip/license.json`, 重装系统/换机会丢 —— 迁移前先备份这一个文件
- 一台机器一个授权码: 机器码由本机硬件指纹生成, 绑定的码换机验签必失败(纪律见《买家红线》)

## 常见问题

- **已激活为什么还出 402?** `probe` 每次先查 `~/.ai-live-clip/license.json`, 命中就走本地验签;
  只有授权文件丢失/换机未迁移/授权过期(购买的是永久授权, 正常不会)才会出账, 先跑 `license` 子命令核对
- **付款后 complete 拿不到授权码?** 先 `status --out-trade-no` 查订单; 结果不明时**不要重复付款**
- **`pay` 没扣款、或报卖家标识不匹配?** ⛔ 先别反复重试。回看 `probe` 的输出：
  若出现 `[服务端预下单失败] …` 或「没有真实预下单号 / 预下单号是本地模拟值」，
  说明**服务端预下单被拒、支付宝侧根本没有这笔单，这笔账单付不了款**——
  把**订单号 + 错误原文**发给作者即可，不要自行改卖家配置、也不要猜收款账号。
  同时确认买家钱包已开通(`alipay-bot check-wallet` 不返回「未开通」)；
  钱包未开通须买家本人扫码授权，⚠ 与上面的预下单失败是**两回事**，别混为一谈。
  服务端 402 响应体里的 `precreate_error` 字段就是错误原文；逐条对照见
  `references/billing-integrator.md`《预下单失败排障》。
- **服务器/网络异常但已买断?** 默认离线放行(`AI_CLIP_OFFLINE_GRACE=1`), 排障时见下表

## 客户端环境变量(一般不设, 排障时用)

| 变量 | 默认 | 用途 |
|---|---|---|
| AI_CLIP_BILLING_URL | 取 config.json 的 service_url | 沙箱 HTTPS 地址; 环境变量优先于配置文件; 两者都空退回本地授权模式 |
| AI_CLIP_RESOURCE_PATH | /api/license | 买断资源端点(须与服务端注册一致) |
| AI_CLIP_PRICE_CNY | 19.90 | 买断价; 与账单不符时打印告警 |
| AI_CLIP_HTTP_RETRIES / _SLEEP | 2 / 3 | 网络故障与 5xx 重试次数 / 间隔秒 |
| AI_CLIP_OFFLINE_GRACE | 1 | 已买断遇服务端故障是否离线放行(置 0 关闭) |
| AI_CLIP_LICENSE_FILE | ~/.ai-live-clip/license.json | 授权文件路径(仅联调覆盖用) |
| ALIPAY_BOT_CMD / _BODY_FLAG / _HEADERS_FLAG | alipay-bot | alipay-bot CLI 适配 |

## 买家红线

- ⛔ 不得伪造或推导授权码、伪造付款凭证; 不得跳过闸口先跑流水线
- ⛔ 已激活时不得再发起付费请求(重复计费)
- ⛔ 不向对话外的人展示支付链接原文、二维码原文或交易号—— 只说金额与"请在收银台完成支付"
