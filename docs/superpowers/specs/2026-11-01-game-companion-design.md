# 普拉娜游戏陪玩设计（MC / GMod「虚拟桌面」方案）

日期：2026-11-01
状态：草稿（建造者检查自查完成，已修订；待老师定稿）

## 意图与背景

老师希望 Prana 不只是聊天框里的助手，而是**直接加入游戏**，作为游戏中的一个角色陪老师玩 Garry's Mod 与 Minecraft。她的「游戏本体」通过「虚拟桌面」方式运行——**不在老师的物理桌面上弹出新窗口、不抢焦点**；游戏画面在老师自己的客户端里，老师在游戏里就能看到普拉娜。

老师原话（范围裁决）：

- 「直接加入游戏」（不是聊天框出主意）
- 「依靠服务器或局域网连接」——**只 join，不指望用户本地起服务器**（无 host 模式）
- 连接逻辑：**先探测本地服务器，探到就直连；探不到就向老师要服务器 IP**
- 「让模型自己决定要启动的 mc 是哪个 mc」——MC 版本不由人定，由模型/服务器握手决定；一期只接 vanilla，NeoForge 模组服到时明说
- 「接受往用户的 GMod 目录装 1 个可删 Lua 文件」——GMod 本体把 1 个 `prana_agent.lua` 装进用户 GMod 安装目录（客户端 + 监听服双侧自动加载，删除即恢复原样）；join 前在审查材料里向老师/审查模型明示（老师裁决 2026-11-01）
- 只适配 **Java 版** MC
- MC 默认皮肤已备好：`C:\Users\Administrator\Downloads\普拉娜.png`（64×32 Java 皮肤）

## 用户批准的范围

- 含：① MC 无头 bot 陪玩（一期）；② GMod 第二客户端陪玩（二期）；③ 本地服务器自动探测 + 要 IP 兜底；④ 虚拟显示器隐藏 GMod 客户端；⑤ 游戏内聊天回路（游戏里说话，软件窗口当后台）。
- 不含：host 模式（本地起服）、主动自动加入（老师不叫不出现）、游戏内语音、软件内嵌游戏画面（老师看游戏里就行）、NeoForge 模组服（三期）。
- 反目标：不做"老师不在时普拉娜自己玩"；不做任意游戏通用框架（只做这两个游戏）。

## 现状（可复用的件 + 机器侦察结果）

| 件 | 位置 | 说明 |
|---|---|---|
| skill 框架 + 审查模型 | `skills/*`、`ai_client.py` | 每个领域一个 skill 模块（`*_SKILLS` + `invoke_*` + `validate_*`）；敏感操作走"操作审查模型"逐次批准（`run_shell_command`、workspace 写、桌面控制同例） |
| 主模型 API | `ai_client.py` | OpenAI 兼容 Chat Completions；游戏大脑直接复用，不新增模型配置 |
| 会话/设置 | `sessions.py`、`settings_store.py` | 游戏会话状态可挂现有会话；新设置字段进 `settings.json`（加密机制沿用） |
| 桌面控制先例 | `skills/desktop.py` | 「先授权后操作」的会话级授权模式，游戏会话可参照 |

机器侦察（本台 Windows）：

- GMod：`D:\Administrator\Steam\steamapps\common\GarrysMod`，`gmod.exe` build 2026.09.16。
- MC：PCL 管理，`D:\mc\.minecraft\versions` 含 **1.20.1**（另有 1.21.1-NeoForge，一期不接）。
- **Node.js v24 + npm 已安装**（`C:\Program Files\nodejs`）→ Mineflayer Node 服务可直接跑。
- **JDK 21 在 PATH**（`C:\Program Files\Microsoft\jdk-21.0.12.8`）→ 需要时可起本地 vanilla 1.20.1 测试服务器。
- **Todesk 与 GameViewer 虚拟显示器驱动已装**，且 ToDesk、GameViewerServer 进程在跑 → GMod 客户端可直接钉在虚拟显示器上，零新增驱动。
- `D:\steamcmd` 存在（备用：拉 GMod 专用服，一期用不上）。

## 设计

### 总架构

```
老师 ──(软件聊天)── Prana 主模型（大脑，配置已有）
                        │  游戏 skill（join/act/stop）
                        ▼
                   游戏会话（本应用内线程）
                  ╱            ╲
        MC 本体（Node）      GMod 本体（客户端+Lua addon）
        Mineflayer 服务      第二份 gmod.exe，窗口钉在虚拟显示器
        无窗口、无 GPU       窗口不可见、不抢焦点
              │                    │
        MC 服务器/局域网  ←——— GMod 服务器/局域网（老师开的）
```

- 一个**游戏会话**对象挂当前会话：老师在游戏里发的消息（MC `/msg`、GMod chat）作为事件回流，显示在游戏会话卡片里；老师的后台指令（软件聊天）通过游戏 skill 转发给本体。
- 会话内动作自动执行（join 本身已过审查模型）；随时可 stop。

### 连接与探测（两游戏共用流程）

1. 老师在游戏 skill 里请求加入（或只说"陪我玩"，模型决定）。
2. **本地探测**：
   - MC：对 `127.0.0.1:25565..25569` 发 Server List Ping（1.20.1 协议）。局域网开世界占用 25565 起第一段空闲端口。
   - GMod：对 `127.0.0.1:27015..27019` 发 GoldSource A2S_INFO。
   - 探到 → 回报版本/地图/玩家数，直接连。
3. 探不到 → skill 返回"未找到本地服务器"，主模型向老师要 IP（正常对话流，不需要新 UI）。
4. MC 版本以**服务器握手返回的 version** 为准；1.20.x 可接，其他 vanilla 版本按 Mineflayer 支持面能接则接；模组服（握手失败/自定义 payload）→ 明确回报"这个服是模组服，一期接不了"。
5. 在线服务器（`online-mode=true` 的公网服）需要微软账号 → 一期不支持，明确回报。

### MC 本体（一期，Node + Mineflayer）

- `prana_mc_body/`（独立 Node 工程，进仓库；首用 `npm ci` 装 mineflayer，本机已有 Node24）：
  - JSON/stdio 服务，本机端口随机。
  - 指令：`connect(host, port, name, skinBase64)` / `disconnect` / `follow(name)` / `say(text)` / `msg(target, text)` / `status()`。
  - 事件：聊天（公屏 + 私聊）、位置、附近玩家、连接错误。
  - 皮肤：`普拉娜.png` 读入 base64 → `bot.applySkin`。默认游戏名 **`Prana`**（vanilla 玩家名只允许 `[a-zA-Z0-9_]`，中文不行；可在设置里改）。
  - **EXE 交付形态**：Node 工程源文件打进 EXE 数据，首用解压到 `%LOCALAPPDATA%\Prana\mc_body\` 再 `npm ci`；开发形态直接用仓库目录。
  - 行为面（一期）：跟随（pathfinder）、说话、私聊、停止。破坏/放置方块**不进一期**（避免误拆老师的世界；三期再开放）。
- EXE 不捆绑 Node：找不到系统 Node 时 Prana 明确回报"本机没有 Node.js，无法运行 MC 本体"。

### GMod 本体（二期）

- **客户端**：启动 `gmod.exe`（自动探测 Steam 安装目录；连接参数形态见 spike S4），挂我们自己的 Lua。
- **Lua 接入**：写 `garrysmod/gamemodes/gmod/includes/extensions/prana_agent.lua`（单文件、客户端 + 监听服双侧自动加载、可删除回退；**该挂载路径为初判，S2 以实测 GMod 客户端/监听服实际加载点为准，必要时换挂载点**）：
  - 监听服侧（= 本机老师的 gmod，同一安装目录）：给普拉娜玩家注册跟随/瞬移用的 server 命令。
  - 客户端侧：状态上报（位置、附近实体、chat 行）、执行 `say`、client 侧 `spawn`（摆道具）。
  - 远程专用服（老师给 IP）：服务端没我们的 addon → **降级**：只能 `say` + client spawn，不能跟随；回报时说明。
  - 默认身体模型：引擎默认人形（Barnaby）；自定义 BA 普拉娜模型放三期。
- **虚拟显示器**（"不创新窗口"的实现）：
  1. ctypes `EnumDisplayMonitors` 枚举显示器，按 `Win32_VideoController` 匹配 Todesk/GameViewer 适配器，取非主屏边界。
  2. 找到 gmod 窗口（SDL 窗口类，构造名见 spike S4）后 `SetWindowPos` 进虚拟屏坐标区，`SWP_NOACTIVATE` 不抢焦点；启动瞬间的焦点抢用"启动前临时 SetForegroundWindow 到 Prana、启动后立即移窗"缓解。
  3. 找不到虚拟屏 → 提示老师开启 Todesk 虚拟显示器设置；再不行 → 本次放弃并说明，不做"第二个 Windows 虚拟桌面"兜底（任务栏会露出，违背"不创新窗口"）。

### 游戏大脑（两游戏共用的 LLM 循环）

- 应用内线程：每游戏会话一个。
- 输入：本体状态快照 + 最近事件（游戏内聊天、老师后台指令）。
- **LLM 调用纯事件驱动**：本体静默时不调模型（零费用）；事件密集时按 ≥4s 间隔合并突发，控调用量与 token。
- 系统提示词：普拉娜人格（复用现有 persona 段落裁剪版）+ 游戏规则 + 动作工具 schema（每游戏一套）+ "游戏内聊天即老师的输入"。
- 动作工具：
  - MC：`mc_follow(name)` / `mc_say(text)` / `mc_msg(target,text)` / `mc_stop`
  - GMod：`gmod_say(text)` / `gmod_spawn(item)` / `gmod_follow(name)` / `gmod_stop`
- 游戏内收到老师 `/msg` → 作为事件驱动一次 LLM 调用；普拉娜在游戏里回话。

### 安全与审查

- `join_game`（含 host/port/name/skin 参数）= 敏感操作，**逐次走现有审查模型**（与 `run_shell_command` 同级）；老师未配置审查模型时 fail-closed。
- 游戏会话内动作自动执行（决策：一次 join 审查覆盖会话内行为，避免每个"走两步"都调一次模型，延迟不可接受）——`stop` 永远可用；header 加"游戏中 · 普拉娜在 <游戏> · 结束"指示器（仿桌面授权指示器）。
- GMod 安装目录会被加 1 个 Lua 文件（可一键删除，删除即恢复原样），join 前在审查材料里向老师/审查模型明示。**老师裁决（2026-11-01）：接受装这 1 个文件。**

### 设置（`settings.json` 新增 `game` 块）

```jsonc
"game": {
  "mc_name": "Prana",
  "mc_skin_path": "C:\\Users\\Administrator\\Downloads\\普拉娜.png",
  "gmod_client_dir": "",        // 空 = 自动探测 Steam 目录
  "gmod_model": ""              // 空 = 引擎默认人形
}
```

- 皮肤首次使用时复制到 `%LOCALAPPDATA%\Prana\assets\普拉娜.png`（不依赖 Downloads 目录长期存在），原文件丢失时回退用副本。

## 涉及模块

| 模块 | 改动 |
|---|---|
| `skills/game/`（新包） | `__init__.py`（`GAME_SKILLS` + `invoke_game_skill` + `validate_game_skill_arguments`，沿用既有 skill 契约）；`probe.py`（MC A2S/SLP 探测）；`agent.py`（大脑循环）；`mc.py`、`gmod.py`（本体驱动）；`vdisplay.py`（显示器枚举 + 移窗） |
| `prana_mc_body/`（新，Node） | package.json + 服务 ~200 行；随仓库分发 |
| `garrysmod/gamemodes/gmod/includes/extensions/prana_agent.lua`（装机产物） | 由应用安装，带卸载开关 |
| `main.py` | 注册 GAME_SKILLS；header 游戏指示器；游戏会话卡片（游戏内聊天流）；join 失败要 IP 的对话兜底 |
| `ai_client.py` | 敏感操作类别加 "game"（审查材料拼装复用现有路径） |
| `settings_store.py` | `game` 块持久化（skin_path 明文路径即可，不涉密） |
| `tests/` | probe 对假服务器（Python socket 实现 MC 握手/A2S 桩）；agent 对假模型；MC 本体对假 Node 服务；vdisplay 枚举逻辑 |

## 约束

- Prana EXE 本身**不新增 Python 依赖**（纯 stdlib + 现有依赖；ctypes/Win32 API 走 stdlib）。
- Node 服务为外部进程，Node.js 为前置条件（本机已有；缺失时明报）。
- 一期 vanilla 1.20.1 为主；1.20.x 其他小版本跟随 Mineflayer 支持面。
- 不引入第二个账号体系（MC 离线模式 / 免 auth 路线；在线公网服明确不支持）。

## Spike（实现前必须完成，一次性、不计入正式实现）

| # | 验证什么 | 方法 | 失败后果 |
|---|---|---|---|
| S1 | Mineflayer Node 服务能 join 本机 vanilla 1.20.1 服（offline-mode），皮肤/跟随/聊天全通 | JDK21 起本地 1.20.1 测试服 + PCL 客户端同服验证 | 换 Minecraft-Server-Protocol 方案或降级行为面 |
| S2 | 双 GMod 实例同机：老师 listen LAN 服 + 普拉娜客户端 `127.0.0.1` 加入 | 手动各开一个 gmod 验证 | 改用专用服路线（steamcmd，需要 Java… 不需要，专用服自带） |
| S3 | Todesk 虚拟显示器在 ToDesk 未连接时是否仍注册为监视器；GMod 窗口能否钉上去且不抢物理桌面焦点 | 驱动级探测 + 实际启动 gmod 移窗 | 换 GameViewer；再不行让老师常开 Todesk |
| S4 | GMod 二次启动的确切 CLI（连接参数 `-ip`/`-connect` 形态、免 Steam 直启、窗口类名） | 读 GMod 启动参数文档 + 实测 | 走 Steam URL 启动 |

## 分期

1. **一期（本计划交付）**：MC 全链路——探测/要 IP、Node 本体、皮肤、跟随/聊天、大脑循环、游戏会话卡片、审查接入。
2. **二期**：GMod 全链路——双实例 + includes Lua + 虚拟显示器 + 跟随/spawn/聊天（远程服降级模式）。
3. **三期（另行设计）**：NeoForge 模组服、自定义 GMod 身体模型、方块破坏/放置开放、软件内嵌游戏画面。

## 留给建造者的决定（标注：这些是我的默认选择，可按工程判断调整，但不改变要构建的东西）

- MC 会话内"破坏/放置方块"默认**不开放**（安全面），只留三期开关
- 游戏会话卡片复用桌面控制的"指示器 + 撤销"交互范式
- 大脑心跳语义（纯事件驱动、突发合并 4s）与 EXE 下 Node 服务落地目录（`%LOCALAPPDATA%\Prana\mc_body\`）
- GMod Lua 挂载点以 S2 实测为准（初判 `includes/extensions`，若 gmod gamemode 不加载该目录则改用 autorun 钩子或独立 gamemode 壳）
- Node 工程随仓库分发、首用 `npm ci`（不预装 node_modules）
- GMod 探测端口段 `27015..27019`、MC 探测 `25565..25569`（各 5 个，局域网开世界实际只可能落在前几个）
- 大脑调用用**主模型**，不新增"游戏专用模型"配置项
- 空闲心跳 4s、单次状态快照 ≤2000 字符（控 token）
- `普拉娜.png` 首用即复制到 Prana 数据目录，原路径仅作来源
