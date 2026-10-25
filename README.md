# 任天堂好友监控（astrbot_plugin_nso_friends）

基于 [NSO-API wiki](https://github.com/sodiumts/NSO-API/wiki) 的 AstrBot 插件：轮询 Nintendo Switch Online 好友状态，好友**开始游玩**或**切换游戏**时，把游戏名、封面、累计时长渲染成卡片推送到群；也可以随时查询某位好友当前在玩什么、玩了多久。

> 非官方插件。Nintendo 没有公开 API，本插件依赖 NSO App 的私有接口，账号使用风险请自行评估。

卡片样式预览：用浏览器打开 [`docs/preview.html`](docs/preview.html)（离线示例数据，不依赖 T2I）。

## 功能

- **轮询监听好友动态**：定时调用 `/v3/Friend/List`，与上一次快照做差分。
  - 好友开始游玩 → 推送游戏卡片
  - 好友切换游戏 → 推送新游戏卡片
  - 好友结束游玩 → 可选推送一条文本（含本次观测时长）
  - 首次启动只建立快照，不会把"当前所有在玩的好友"刷屏一遍
- **游戏卡片渲染**：游戏封面 + 好友头像 + 游戏名 + 官方累计时长 + 首次游玩 + 本次开始时间，通过 AstrBot T2I 输出 PNG；渲染失败自动降级为文本 + 封面图。
- **好友查询**：`/nso 查询 <好友>` 出详情卡片；`/nso 记录 <好友>` 查看官方累计时长与插件观测到的游玩区间。
- **分群订阅**：每个群/私聊独立开关，可只监控指定好友；登录后自动订阅当前会话。
- **多账号隔离**：一个 AstrBot 实例绑定一个 Nintendo 账号；数据按插件目录隔离。

## 安装

1. 确认 AstrBot 版本 ≥ 4.24.2，且可用的 T2I 渲染服务（AstrBot 默认自带；没有也能用，只是降级为文本）。
2. 把本目录放到 `data/plugins/astrbot_plugin_nso_friends/`。
3. 在 AstrBot WebUI「插件管理」中重载插件，或在控制台重启 AstrBot。
4. 依赖：`httpx`（AstrBot 已内置，`requirements.txt` 仅作声明）。

## 首次登录（只需一次）

登录流程沿用 NSO App 的 PKCE 授权，**session_token 有效期约 2 年**，之后插件会自动用它在后台换取 2 小时有效的 `webApiServerCredential`。

1. 在**群聊或私聊**中发送：

   ```
   /nso 登录
   ```

   机器人会返回一个 `https://accounts.nintendo.com/connect/1.0.0/authorize?...` 链接。

2. 在已登录 Nintendo 账号的浏览器里打开该链接，完成登录，点击「选择此人 / Select this person」。
3. 在按钮上**右键 → 复制链接地址**，得到形如：

   ```
   npf71b963c1b7b6d119://auth#session_state=...&session_token_code=...&state=...
   ```

4. 把整段链接粘回**同一个会话**：

   ```
   /nso 登录 npf71b963c1b7b6d119://auth#session_token_code=...&state=...
   ```

5. 成功后会提示账号昵称与好友码，并**自动订阅当前会话**的推送。

注意事项：

- 登录/解绑需要管理员权限。
- 授权链接 15 分钟内有效；同一个链接只能使用一次，失败请重新 `/nso 登录`。
- 如果登录发生在私聊，群不会自动订阅；到群里发一次 `/nso 订阅` 即可。
- 想让某个群默认订阅，也可以把它的 UMO（如 `aiocqhttp:GroupMessage:123456789`）填进配置 `default_subscriptions`。

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

`/nso` 也可写作 `/任天堂`；`/nso 张三` 会直接按好友名查询。

好友名支持大小写不敏感的子串匹配；重名时机器人会列出候选，补全名字即可。

## 配置

在 AstrBot WebUI 的插件配置页修改，均带默认值：

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `poll_interval` | int | `300` | 轮询间隔（秒），最小 60。建议 180–600 |
| `enable_push` | bool | `true` | 总开关；关闭后不自动拉取，也不积累本地记录 |
| `notify_game_start` | bool | `true` | 推送"开始游玩" |
| `notify_game_change` | bool | `true` | 推送"切换游戏" |
| `notify_game_stop` | bool | `false` | 推送"结束游玩"（文本） |
| `render_card` | bool | `true` | 是否渲染卡片图片 |
| `default_subscriptions` | list | `[]` | 启动时自动订阅的会话 UMO |
| `app_version` | string | `2.11.0` | NSO App 版本号，登录报 9403/401 时更新 |
| `f_api_url` | string | `https://api.imink.app/f` | f 参数服务；可填多个地址（逗号/空格分隔）按顺序尝试 |
| `f_api_headers` | dict | `{}` | f 请求附加头（JSON），授权服务/自建服务需要时填写 |
| `timezone` | string | `Asia/Shanghai` | 卡片与记录展示时区 |
| `max_push_per_cycle` | int | `6` | 单轮最多推送条数，防止刷屏 |
| `friend_list_limit` | int | `25` | `/nso 好友` 显示上限 |
| `render_timeout` | int | `90` | 单张卡片渲染超时（秒） |
| `request_timeout` | float | `30` | HTTP 请求超时（秒） |

## 推送逻辑

插件维护每位好友的状态快照：

```
{playing, game, game_since, state, updated_at, seen_at}
```

- **首轮**（本地还没有任何快照）：只写入状态，不推送。
- 好友从「未游玩」进入「游玩」→ `started`。
- 好友从「游戏 A」变为「游戏 B」→ `changed`，同时把 A 的游玩区间落库。
- 好友从「游玩」变为「未游玩/离线」→ `stopped`，把本次区间落库（默认不推送）。
- 插件重启后沿用快照：同一款游戏不会重复推送，停机期间发生的状态变化会在下一轮补报。

推送只发给订阅了该会话、且名字过滤命中的群/私聊；每轮超过 `max_push_per_cycle` 的条数会跳过并写日志。

## 关于"游戏记录"

Nintendo 的 `/v3/Friend/List` 只暴露**当前正在玩的游戏**的 `totalPlayTime`（累计分钟数）与 `firstPlayedAt`，并不提供好友的完整游戏历史。因此：

- `/nso 记录 <好友>` 的"官方累计"只对**当前游戏**有意义；
- 插件会把每轮观测到的游玩区间写入本地 `sessions`，从而给出"近 7 天/30 天次数与时长""最近若干次游玩记录"；
- 这些本地记录依赖轮询：`enable_push` 关闭或插件长期停机时不会积累。

## 数据与隐私

数据保存在 `data/plugin_data/astrbot_plugin_nso_friends/`：

- `nso_state.json`：`session_token`、账号昵称/好友码、订阅、好友快照、本地游玩记录；
- `images/`：封面与头像缓存。

`session_token` 等同于 Nintendo 账号的长期登录凭证：

- 不要把这个目录提交到公开仓库或分享给他人；
- 怀疑泄露时立即 `/nso 登出`，并到 Nintendo 账号设置中退出所有设备；
- f 参数由第三方服务（默认 imink）根据 `id_token` 计算，`id_token` 不含密码但属于账号凭证；介意的话可自建 [f-API](https://github.com/imink-app/f-API) 并修改 `f_api_url`。

插件不会记录或输出 token 到日志。

## 常见问题

**登录返回 9403 / 401**
Nintendo 更新 App 后，旧版本号会被拒绝。到 Google Play / App Store 查看 NSO 最新版本，把配置 `app_version` 改成对应版本号，再重新 `/nso 登录`。

**f 参数服务 500 / 401 / 429**
imink 公共实例自 2024-12 起长期不可用（Nintendo Play Integrity），返回 500 属服务端问题，换时间重试也不会恢复；nxapi 公共实例返回 401 表示需要授权。处理方式见上文「f 参数服务（登录必需）」：申请 nxapi 授权、自建服务，或等待 imink 恢复。

**卡片没渲染出来，只有文字**
检查 AstrBot 的 T2I 服务；渲染超时/失败会自动降级，属预期行为。可关闭 `render_card` 避免每次等超时。

**群里没有推送**
确认在**该群**发过 `/nso 订阅`（私聊登录不会自动订阅群），并用 `/nso 状态` 查看登录态与轮询是否正常。

**好友显示"在线"但没有游戏**
对方可能设置了不公开游玩状态，或只是停留在主界面；NSO 此时 `presence.game` 为空。

**轮询间隔能更短吗？**
不建议低于 180 秒。Nintendo 对高频请求可能限流，且插件按区间记录时长的精度也依赖间隔。

## f 参数服务（登录必需）

NSO 登录必须携带 `f` 参数。它不是 Nintendo 的公开接口，需要由运行 NSO App 的 Android 设备/模拟器现场生成，因此插件依赖第三方或自建的 f 服务。

当前（2026-10）实测情况：

- **imink 公共实例**（配置默认 `https://api.imink.app/f`）：自 2024-12 Nintendo 更新 App 引入 Play Integrity 后长期不可用，任意 token（包括无效 token）都返回 `HTTP 500 {"reason":"Something went wrong."}`。上游说明见 [imink-app/f-API#5](https://github.com/imink-app/f-API/issues/5)。这不是插件 bug。
- **nxapi-znca-api**：仍在维护，但公共实例自 2025-06 起需要 [nxapi-auth](https://nxapi-auth.fancy.org.uk) 授权；需要注册客户端并向作者申请开通（[Discord](https://discord.com/invite/4D82rFkXRv)），然后在 `f_api_url` 填入 `https://nxapi-znca-api.fancy.org.uk/api/znca/f`，并用 `f_api_headers` 配置 `Authorization` 与 `X-znca-*` 头。
- **自建 f 服务**：按 [nxapi-znca-api](https://github.com/samuelthomas2774/nxapi-znca-api) 的说明，用 root Android / redroid + NSO App + Frida 部署；然后把 `f_api_url` 指向 `http://<host>:<port>/api/znca/f`，必要时用 `f_api_headers` 传授权头。

`f_api_url` 支持用逗号或空格分隔多个地址，插件会按顺序尝试；`f_api_headers` 是 JSON 对象，会附加到每次 f 请求上。

> 登录失败不会丢失登录态：`session_token` 在换取阶段就已保存。f 服务恢复或更换后，执行 `/nso 检查` 或等待下一轮轮询即可自动完成登录，不需要重新打开授权链接。

## 与 wiki 的两处差异

实现严格按 wiki 的参数与流程，但修正了两处会导致请求失败的笔误：

1. `session_token` 换取接口实际是 `POST` + `application/x-www-form-urlencoded`，wiki 写成了 `GET` + JSON body；
2. `/v3/Account/Login` 的 `naIdToken` 与 imink 的 `token` 都应为 `id_token`（wiki 正文写 access_token，但示例 JWT 的 `typ` 为 `id_token`）。

其余端点、Header、`f` 参数流程均与 wiki 一致。

## 开发与测试

插件把 Nintendo 协议层与 AstrBot 解耦，`nso/` 下不 import 框架，可直接单测；测试用假传输层模拟完整的登录与好友接口：

```bash
cd data/plugins/astrbot_plugin_nso_friends
python3 -m unittest discover -s tests -t . -v
```

覆盖：PKCE/回调解析、登录换 token、凭证缓存与 401 重登、好友模型、状态差分与重启续接、会话记录、图片缓存、HTML 转义、降级文本，以及用 AstrBot 桩模块跑通的指令与推送链路。

## 免责声明

本项目与 Nintendo 无关联。使用非官方 API 可能违反 Nintendo 服务条款，存在账号被限制的风险；请仅用于个人学习与自建机器人，不要用于商业用途或大规模抓取。
