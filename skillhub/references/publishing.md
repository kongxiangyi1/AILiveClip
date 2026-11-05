# 发布与更新到 SkillHub（仅开发者）

> 日常使用本技能完全不涉及发布流程。这份文档记录的是把本包推上 SkillHub 的官方 CLI 路径、
> frontmatter 硬性要求与常见错误码。

---

## 发布到 SkillHub(官方 CLI 流程)

> 依据 `skillhub.cn/tutorials`「通过 CLI 和 Agent 发布 Skill」(2026-10-31 核对)。
> 网页端一次性和少量发布足够; 经常迭代建议走 CLI。

**前置(浏览器, 一次性)**

1. 手机号验证码登录/注册 → 右上角头像 → 个人中心 → **实名认证**(人脸核身)
   ⛔ 未实名无法创建 API Token, 也无法发布
2. 个人中心 → `API keys`(直链 `/dashboard/keys`) → 创建, 得到 `skh_xxx`
   ⚠ **只显示一次**, 立刻保存; 丢了删掉重建即可

**CLI**

```bash
skillhub login --key <skh_xxx> --host https://api.skillhub.cn   # ✓ Logged in as @handle
skillhub auth whoami                                            # 校验 userId/handle/role
skillhub publish <skill目录> --host https://api.skillhub.cn --dry-run   # 先预检
skillhub publish <skill目录> --host https://api.skillhub.cn --changelog "首次发布"
```

成功输出 `✓ Published: skillId=xxxxx status=pending_review`, 进审核;
审核通过后详情页可见。**更新**时保持 `slug` 不变, 改 `version` 并重发即可。

**frontmatter 要求**(本包已满足)

| 字段 | 要求 | 本包 |
|---|---|---|
| `slug` | 必填 · kebab-case · 2-128 字符 · **全网唯一** | `ai-live-clip` |
| `version` | 必填 · 合法 SemVer | 见本文件 frontmatter `version`(唯一来源, 正文不重复写死) |
| `displayName` | 必填 | `直播切片工具` |
| `summary`/`description`/`tags`/`license` | 建议有, 不作为阻断 | 均已填 |

⚠️ `slug: ai-live-clip` 是通用词, **很可能已被占用** → 发布报 `409 slug 已被其他用户占用`。
建议改成带个人标识的, 例如 `ai-live-clip-<你的handle>`(改 `slug` 不影响技能内部逻辑)。

**常见错误**: `401 invalid api key`(Token 失效, 重建) / `403 请先完成实名认证` /
`409 slug 冲突`(改 slug) / `429 请求过于频繁`(等 1 分钟) / 发布后详情页"未找到"(审核中, 属正常)。
