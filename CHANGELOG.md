# 更新日志

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## v2.2.0 — 2026-11-02

### 新增

- 新增本地 **Pillow 卡片渲染**（`render_backend: pillow`，默认），不再依赖 AstrBot T2I，解决 `t2i-local` 只支持 Markdown、无法渲染 HTML 模板导致的“卡片渲染失败”。
- 新增 `render_backend` 配置：`pillow` / `auto` / `t2i`；T2I 失败时自动回退到 Pillow。
- 图片缓存新增 `image_bytes()`，封面/头像只下载一次，同时供 T2I 与 Pillow 使用。

### 修复

- 使用只支持 Markdown 的本地 T2I 服务时，卡片会直接降级为文本的问题。

## v2.1.0 — 2026-11-02

### 新增

- 管理员好友管理命令：
  - `/nso 加好友 <好友码>`：按好友码查人，二次确认后才发送申请
  - `/nso 确认加好友` / `/nso 取消加好友`
  - `/nso 好友申请`、`/nso 接受申请 <ID>`、`/nso 拒绝申请 <ID>`
  - `/nso 已发申请`、`/nso 取消申请 <ID>`
- 好友申请相关操作全部限制为管理员。

### 说明

- 发送好友申请只是发起请求，对方仍需在 Switch/App 接受。
- 不要在短时间内批量加好友，Nintendo 可能限流或风控。

## v2.0.1 — 2026-11-02

### 修复

- 支持 nxapi-auth **Shared secret + HS256 client assertion**，不再依赖尚未开放的 client_secret 页面；新增 `f_api_shared_secret` 配置。
- Coral v4 请求体不再发送 `requestId`（否则 nxapi 返回 `invalid_request`）。
- `encrypt-request` 的 `url` 改用 path（如 `/v4/Friend/List`），之前误传完整 URL 会导致生成无效密文。

### 文档

- 补充客户端 `Scope` 中 `nxapi-znca-api` 分组（f-generation / Request encryption / Response decryption）的勾选与保存步骤。
- 更新 FAQ：`invalid_scope: unknown scope "ca:gf"` 的排查方法。

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
