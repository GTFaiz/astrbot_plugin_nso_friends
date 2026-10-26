# 更新日志

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## v2.0.0 — 2026-10-26

**破坏性变更**：协议从 v3 明文接口迁移到 Coral 3.5.0 / nxapi 加密协议，v1 的配置与登录方式不再适用。

### 新增

- 支持 Coral 3.5.0 加密协议：请求体经 f 服务的 `encrypt-request` 加密，响应经 `decrypt-response` 解密。
- 登录改为 `POST /v4/Account/Login`（`application/octet-stream`），业务接口改为 `/v4/Friend/List`。
- f / 加解密服务支持 OAuth 2 `client_credentials`：自动换取并缓存 access token，支持 `f_api_client_id`、`f_api_client_secret`、`f_api_scope`（默认 `ca:gf ca:er ca:dr`）、`f_api_token_url` 配置。
- 新增 `f_api_client_version` 配置，对应 `X-znca-Client-Version` 请求头。
- 凭证过期时自动恢复：HTTP 401 或业务码 9404 会用现有 `id_token` 重新换取 `webApiServerCredential` 并重试一次，连续失败才要求 `/nso 登录`。
- `f_api_url` 指向自建服务时，自动推导同源的 `encrypt-request` / `decrypt-response` 地址。

### 变更

- 默认 f 服务由 imink 公共实例 `https://api.imink.app/f` 改为 nxapi 公共实例 `https://nxapi-znca-api.fancy.org.uk/api/znca/f`。
- 默认 `app_version` 由 `2.11.0` 提升至 `3.5.0`，`X-znca-Version` 等请求头同步。
- `f_api_headers` 说明改为"自建服务/需额外授权时填写"，不再作为对接 nxapi 的主要方式。
- README 重写：新增协议版本说明、nxapi OAuth 客户端准备步骤、升级与排错指引；插件市场元数据描述同步为 Coral 3.5 / nxapi。

### 测试

- 测试用假服务升级为 nxapi + Coral v4 仿真，覆盖 OAuth `client_credentials`、f 请求、加解密、v4 登录、好友列表、401/9404 重登、状态差分与卡片渲染，共 54 个用例。

### 升级提示

1. 重新配置 `f_api_url`，填入 nxapi 公共实例或自建服务地址。
2. 使用公共实例时，在 <https://nxapi-auth.fancy.org.uk/oauth/clients> 创建 Confidential 客户端并申请 scopes，把 `client_id` / `client_secret` 填入插件配置。
3. `session_token` 仍然有效，**不需要**重新走一遍 Nintendo 授权；但 f 服务不可用时无法完成登录换取。
4. 旧的 `app_version`、`f_api_headers` 值请按新默认值调整。

## v1.0.0 — 2026-10-25

首个公开版本。

### 新增

- 轮询 `/v3/Friend/List`，与上一轮快照差分：好友开始游玩、切换游戏、结束游玩时推送卡片或文本。
- T2I 游戏卡片：封面、头像、游戏名、官方累计时长、首次游玩、本次开始时间；渲染失败自动降级为文本 + 封面图。
- 指令：`/nso 登录`、`登出`、`好友`、`查询`、`记录`、`订阅`、`退订`、`订阅列表`、`状态`、`检查`、`我`、`帮助`。
- 分群订阅与指定好友监控；多账号数据隔离；重启后续接快照。
- PKCE 授权登录，`session_token` 持久化，自动换取 2 小时有效的 `webApiServerCredential`。
