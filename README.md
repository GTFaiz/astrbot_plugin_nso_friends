# 任天堂好友监控（astrbot_plugin_nso_friends）

AstrBot 插件：轮询 Nintendo Switch Online 好友状态，好友**开始游玩**或**切换游戏**时，把游戏名、封面、累计时长渲染成卡片推送到群；也可以随时查询某位好友当前在玩什么、玩了多久。

> 非官方插件。Nintendo 没有公开 API，本插件依赖 NSO App 的私有接口，账号使用风险请自行评估。

卡片样式预览：用浏览器打开 [`docs/preview.html`](docs/preview.html)（离线示例数据，不依赖 T2I）。

## 协议版本（重要）

本仓库 v2.0.0 起已切换到 **Coral 3.5.0 / nxapi 加密协议**，不再使用 2023 年 [NSO-API wiki](https://github.com/sodiumts/NSO-API/wiki) 里的 v3 明文接口：

- `/v4/Account/Login`、`/v4/Friend/List`
- 所有 Coral 请求体和响应都经 `/encrypt-request`、`/decrypt-response` 加密/解密
- `f` 参数由 [nxapi-znca-api](https://github.com/samuelthomas2774/nxapi-znca-api) 生成
  （源码在 [GitLab](https://gitlab.fancy.org.uk/samuel/nxapi)）
- OAuth 2 `client_credentials`，scope 为 `ca:gf ca:er ca:dr`
- `X-znca-Version: 3.5.0`、`X-znca-Client-Version` 等请求头

因此插件**必须**配置一个可用的 nxapi f/加解密服务，并拿到对应客户端凭据；纯 session_token 无法完成登录。

## 功能

- **轮询监听好友动态**：定时调用 `/v4/Friend/List`，与上一次快照做差分
  - 好友开始游玩 → 推送游戏卡片
  - 好友切换游戏 → 推送新卡片，并把上一段游玩区间写入本地记录
  - 好友结束游玩 → 可选文本推送（含本次观测时长）
  - 首次启动只建立快照，不会把当前所有在玩的好友一次性刷屏
- **游戏卡片渲染**：游戏封面 + 好友头像 + 游戏名 + 官方累计时长 + 首次游玩 + 本次开始时间，通过 AstrBot T2I 输出 PNG；失败自动降级为文本 + 封面图
- **好友查询**：`/nso 查询 好友` 出详情卡片；`/nso 记录 好友` 显示官方累计时长 + 本地观测到的游玩区间
- **分群订阅**：每个群/私聊独立开关，可只监控指定好友；登录后自动订阅当前会话
- **凭证维护**：`webApiServerCredential` 过期或返回 `9404` 时自动重新登录

## 安装

1. 确认 AstrBot 版本 ≥ 4.24.2，且有可用的 T2I 渲染服务（没有也能用，只是降级为文本）
2. 把本目录放到 `data/plugins/astrbot_plugin_nso_friends/`
3. 在 AstrBot WebUI「插件管理」重载插件，或重启 AstrBot
4. 依赖：`httpx`（AstrBot 已内置）

## 准备 nxapi f 服务

插件默认使用公共实例：

```
https://nxapi-znca-api.fancy.org.uk/api/znca/f
```

该服务需要 OAuth 客户端凭据。流程：

1. 在 <https://nxapi-auth.fancy.org.uk/oauth/clients> 用 Discord 登录，创建一个 **Confidential** 客户端
2. 在 `Scope` 里勾选 `ca:gf`、`ca:er`、`ca:dr`（显示 Requires approval 属正常）
3. 到 [nxapi Discord](https://discord.com/invite/4D82rFkXRv) 的 `#nxapi-auth` 说明用途并申请开通
4. 拿到 `client_id` / `client_secret` 后填进插件配置

> 当前 nxapi-auth 网页端还没有生成 client secret 的界面（页面会明确提示 managing client secrets is not available yet），需要由服务维护者在后台签发。把 `client_id`、用途和 scopes 发给作者即可。

如果你不想用公共实例，可以自建 [nxapi-znca-api](https://github.com/samuelthomas2774/nxapi-znca-api)（需要 root Android / redroid + NSO App + Frida），然后把 `f_api_url` 指向内网地址，例如：

```
http://192.168.0.3:12345/api/znca/f
```

自建服务默认不需要 OAuth，`f_api_client_id` / `f_api_client_secret` 留空即可。

## 首次登录（只需一次）

1. 在群里或私聊发送：

   ```
   /nso 登录
   ```

2. 在已登录 Nintendo 账号的浏览器里打开返回的链接，完成登录，点击「选择此人」
3. 在按钮上右键 →「复制链接地址」，得到 `npf71b963c1b7b6d119://auth#...`
4. 把整段链接粘回同一个会话：

   ```
   /nso 登录 npf71b963c1b7b6d119://auth#session_token_code=...&state=...
   ```

5. 成功后提示账号昵称与好友码，并自动订阅当前会话

注意事项：

- 登录/解绑需要管理员权限
- 授权链接 15 分钟内有效，同一个链接只能用一次
- 私聊登录不会自动订阅群，到群里发一次 `/nso 订阅`
- `session_token` 约 2 年有效，之后登录由插件自动完成

## 指令

| 指令 | 说明 | 权限 |
| --- | --- | --- |
| `/nso 登录` | 生成 Nintendo 授权链接 | 管理员 |
| `/nso 登录 <回调链接>` | 完成绑定 | 管理员 |
| `/nso 登出` | 清除本地登录态与好友快照 | 管理员 |
| `/nso 好友` | 好友在线/游玩概览 | 所有人 |
| `/nso 查询 <好友>` | 好友当前游戏详情卡片 | 所有人 |
| `/nso 记录 <好友>` | 官方累计时长 + 本地观测记录 | 所有人 |
| `/nso 订阅` | 本会话订阅全部好友动态 | 所有人 |
| `/nso 订阅 好友A,好友B` | 只监控指定好友 | 所有人 |
| `/nso 退订` | 关闭本会话推送 | 所有人 |
| `/nso 订阅列表` | 查看本会话订阅状态 | 所有人 |
| `/nso 状态` | 登录、凭证、轮询、推送状态 | 所有人 |
| `/nso 检查` | 立即拉取一次并推送 | 管理员 |
| `/nso 我` | 当前绑定的 Nintendo 账号 | 所有人 |
| `/nso 帮助` | 指令列表 | 所有人 |

`/nso` 也可写作 `/任天堂`；`/nso 张三` 会直接按好友名查询。好友名支持大小写不敏感的子串匹配，重名时会列出候选。

## 配置

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `poll_interval` | int | `300` | 轮询间隔（秒），最小 60 |
| `enable_push` | bool | `true` | 总开关；关闭后不自动拉取，也不积累本地记录 |
| `notify_game_start` | bool | `true` | 推送"开始游玩" |
| `notify_game_change` | bool | `true` | 推送"切换游戏" |
| `notify_game_stop` | bool | `false` | 推送"结束游玩"（文本） |
| `render_card` | bool | `true` | 是否渲染卡片图片 |
| `default_subscriptions` | list | `[]` | 启动时自动订阅的会话 UMO |
| `app_version` | string | `3.5.0` | NSO App 版本号，必须与 f 服务一致 |
| `f_api_url` | string | `https://nxapi-znca-api.fancy.org.uk/api/znca/f` | f/加解密服务地址 |
| `f_api_client_id` | string | 空 | nxapi-auth 客户端 ID |
| `f_api_client_secret` | string | 空 | nxapi-auth 客户端密钥 |
| `f_api_scope` | string | `ca:gf ca:er ca:dr` | OAuth scope |
| `f_api_token_url` | string | `https://nxapi-auth.fancy.org.uk/api/oauth/token` | OAuth token 地址 |
| `f_api_client_version` | string | `d8fAZDPzwimzQ7c6` | `X-znca-Client-Version`，默认值借用 nxapi 的兼容版本常量 |
| `f_api_headers` | dict | `{}` | f 请求附加头（自建服务/静态 token 时使用） |
| `timezone` | string | `Asia/Shanghai` | 卡片与记录展示时区 |
| `max_push_per_cycle` | int | `6` | 单轮最多推送条数 |
| `friend_list_limit` | int | `25` | `/nso 好友` 显示上限 |
| `render_timeout` | int | `90` | 卡片渲染超时（秒） |
| `request_timeout` | float | `30` | HTTP 请求超时（秒） |

`encrypt-request` / `decrypt-response` 会自动从 `f_api_url` 推导（去掉末尾的 `/f`）。

## 推送逻辑

插件维护每位好友的状态快照：

```
{playing, game, game_since, state, updated_at, seen_at}
```

- **首轮**（本地还没有快照）：只写入状态，不推送
- 好友从「未游玩」进入「游玩」→ `started`
- 好友从「游戏 A」变为「游戏 B」→ `changed`，同时把 A 的游玩区间落库
- 好友从「游玩」变为「未游玩/离线」→ `stopped`，把本次区间落库（默认不推送）
- 插件重启后沿用快照：同一款游戏不会重复推送，停机期间发生的变化会在下一轮补报

推送只发给订阅了该会话、且名字过滤命中的群/私聊；每轮超过 `max_push_per_cycle` 的条数会跳过并写日志。

## 关于"游戏记录"

Nintendo 的 `/v4/Friend/List` 只暴露**当前正在玩的游戏**的 `totalPlayTime`（累计分钟）与 `firstPlayedAt`，不提供好友的完整游戏历史。因此：

- `/nso 记录 <好友>` 的"官方累计"只对**当前游戏**有意义
- 插件把每轮观测到的游玩区间写入本地 `sessions`，给出"近 7 天/30 天次数与时长""最近若干次游玩记录"
- 这些本地记录依赖轮询：`enable_push` 关闭或插件长期停机时不会积累

## 数据与隐私

数据保存在 `data/plugin_data/astrbot_plugin_nso_friends/`：

- `nso_state.json`：`session_token`、账号昵称/好友码、订阅、好友快照、本地游玩记录
- `images/`：封面与头像缓存

需要注意：

- `session_token` 等同 Nintendo 账号的长期登录凭证，不要提交到公开仓库或分享
- 使用 nxapi 公共实例时，Nintendo Account 的 `id_token`、Coral token 以及请求/响应数据会发送给该第三方服务；插件在登录前会提示这一点
- `client_secret` 只保存在 AstrBot 配置中，不会写入日志
- 怀疑泄露时立即 `/nso 登出`，并到 Nintendo 账号设置中退出所有设备

## 常见问题

**登录提示 `invalid_client` / `Missing client authentication`**
nxapi-auth 还没有为你的客户端签发 client secret。到 `#nxapi-auth` 提供 client_id 和用途，让维护者签发；拿到后填 `f_api_client_secret`。

**f 接口返回 `401 unauthorised`**
客户端的 API 访问还没被批准，或 token 失效。确认 `f_api_client_id` / `f_api_client_secret` 正确，并在 Discord 确认已开通。

**f 接口返回 `403 insufficient_scope`**
scope 没批全。需要 `ca:gf`、`ca:er`、`ca:dr` 三个。

**f 接口返回 `incompatible_client`**
`X-znca-Client-Version` 不匹配。把 `f_api_client_version` 改成 nxapi-auth 为你的客户端签发的值（没有就保持默认）。

**f 接口返回 `unsupported_version`**
`app_version` 与 f 服务支持的版本不一致。看 `https://nxapi-znca-api.fancy.org.uk/api/znca/config` 的 `nso_version`，当前是 `3.5.0`。

**`Coral 接口返回 status=9404`**
凭证过期，插件会自动重新登录；如果连续出现，检查 `session_token` 是否被 Nintendo 注销。

**卡片没渲染出来，只有文字**
检查 AstrBot 的 T2I 服务；渲染超时/失败会自动降级，属预期行为。可关闭 `render_card` 避免每次等超时。

**群里没有推送**
确认在**该群**发过 `/nso 订阅`（私聊登录不会自动订阅群），并用 `/nso 状态` 查看登录态与轮询是否正常。

**好友显示"在线"但没有游戏**
对方可能设置了不公开游玩状态，或只是停留在主界面；NSO 此时 `presence.game` 为空。

## 开发与测试

`nso/` 下不 import AstrBot，可直接单测；测试用假 nxapi + 假 Coral 服务模拟 OAuth、f、加解密和 v4 接口：

```bash
cd data/plugins/astrbot_plugin_nso_friends
python3 -m unittest discover -s tests -t . -v
```

覆盖：PKCE/回调解析、OAuth client_credentials、f 请求、encrypt/decrypt、v4 登录、好友列表、401/9404 重登、状态差分、卡片渲染与降级。

## 更新日志

完整记录见 [`CHANGELOG.md`](CHANGELOG.md)。

### v2.0.0（2026-10-26）

- **协议升级**：迁移到 Coral 3.5.0 / nxapi 加密协议，改用 `/v4/Account/Login`、`/v4/Friend/List`，请求与响应均加解密；v3 明文接口已不可用
- **新增配置**：nxapi OAuth 客户端凭据 `f_api_client_id`、`f_api_client_secret`、`f_api_scope`、`f_api_token_url`，以及 `f_api_client_version`
- **默认值调整**：f 服务默认改为 nxapi 公共实例，`app_version` 默认提升到 `3.5.0`
- **自动重登**：HTTP 401 或业务码 9404 时自动重新换取 `webApiServerCredential` 并重试一次
- **测试**：假服务升级为 nxapi + Coral v4 仿真，54 个用例全部通过
- **升级提示**：需重新配置 f 服务与客户端凭据；`session_token` 仍有效，不必重新授权 Nintendo 账号

### v1.0.0（2026-10-25）

- 首个公开版本：基于 v3 明文接口的轮询监听、T2I 卡片渲染、分群订阅与好友查询

## 免责声明

本项目与 Nintendo 无关联。使用非官方 API 可能违反 Nintendo 服务条款，存在账号被限制的风险；请仅用于个人学习与自建机器人，不要用于商业用途或大规模抓取。使用 nxapi 公共 API 时还需遵守其 [public API terms](https://github.com/samuelthomas2774/nxapi-znca-api/blob/docs/docs/public-api-terms.md)。
