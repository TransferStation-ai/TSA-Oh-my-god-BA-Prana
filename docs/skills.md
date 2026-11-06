# Skills 说明

本文集中介绍 Prana 的 skill 概念、当前工具目录及其执行边界。这里的 skill 指模型可通过 tool calling 请求应用执行的具名操作，不是任意代码执行权限。

## 工作方式

1. `skills\` 中的模块定义工具名称、用途和参数 schema。
2. `ai_client.py` 根据当前功能和会话状态向模型提供相应工具；模型服务须兼容 OpenAI Chat Completions tool calling。
3. 应用校验工具名称和参数，再由对应的 `invoke_*` 函数分发执行。拒绝不支持的参数和操作。
4. 对需要审查的操作，应用会在执行前调用设置中的操作审查模型；没有配置、审查拒绝或结果无效时，操作不会执行。桌面控制还需要用户在授权窗口中明确允许。

工具是否可用可能取决于当前状态，例如是否已经加入 Minecraft、是否有已保存的 MC 技能或是否取得桌面控制授权。除工具描述要求外，模型只应在用户请求相应操作时调用工具。具体参数约束以代码中的 schema 和校验器为准。

## 内置 Skills 目录

| 用途 | 工具 | 说明 |
| --- | --- | --- |
| 对话与时间 | `send_chat_message`、`get_system_time` | 在最终答复前发送一条简短消息；或获取本机当前日期和时间。 |
| 网页图片与媒体 | `send_local_media`、`send_web_image`、`send_image`、`send_video` | 由用户从文件选择器挑选本机媒体、发送当前网页候选图片，或生成图片/视频。本机媒体选择只展示文件，不读取或上传所选文件；生成可能产生 API 费用。 |
| 项目与工作区 | `get_project_structure`、`search_workspace_files`、`write_workspace_file`、`edit_workspace_file`、`transfer_workspace_file`、`switch_workspace` | 查看项目结构、搜索文件、创建/局部修改文件、在永久与临时工作区间复制文件，以及切换永久工作区。路径受工作区范围约束；读写及切换等操作须通过操作审查。 |
| 终端与命令 | `run_terminal_command`、`run_shell_command` | 前者只允许有限的只读目录/文件查询；后者通过 PowerShell 执行命令，范围更广，须经审查，且在当前 Windows 账户权限下运行。 |
| Git | `read_git_repository` | 只读查看状态、日志、差异或分支；不会暂存、提交、推送、切换分支或修改文件。Git 仅用于永久工作区。 |
| 桌面 | `request_desktop_control`、`control_desktop` | 先请求授权，再查看屏幕或操作已打开的窗口。需要用户授权和操作审查；不会自动启动程序，Esc 长按可紧急停止。 |
| QQ 音乐 | `control_qq_music` | 控制已运行的 QQ 音乐，包括播放、切歌、音量和搜索；搜索本身不会自动播放，搜索并播放会尝试确认播放结果。 |
| Minecraft | `probe_game_server`、`join_game`、`game_command`、`leave_game`、`search_minecraft_wiki` | 探测/加入 Minecraft Java 服务器、执行陪玩动作、结束游戏会话，以及搜索中文 Minecraft Wiki。加入服务器须通过操作审查；百科搜索只返回有限条目和摘要。 |
| 建模 | `start_modeling_job`、`open_modeling_reference`、`run_blender_python`、`render_modeling_preview`、`compile_gmod_model`、`modeling_status`、`stop_modeling_job` | 启动 Blender 建模作业、查看参考模型、执行建模代码、渲染预览、编译 GMod 玩家模型和管理作业。`run_blender_python` 在当前用户权限下执行，不是安全沙箱。 |

### Minecraft 自定义技能

MC 游戏大脑可以创建并保存可复用的动作序列。`mc_create_skill` 提交新技能，保存前必须通过操作审查；保存的技能会以 `mc_run_skill` 暴露给后续游戏会话。实现位于 `skills\game\custom_skills.py`：

- 技能代码必须只定义 `run(api)`，函数体由 1–16 个顺序白名单动作组成。
- 动作参数仅接受文本或数字常量；不支持循环、条件、导入、任意 Python、文件访问或终端操作。
- 技能最多保存 32 个，描述最多 160 个字符；存放于 `%LOCALAPPDATA%\Prana\game_skills`。

内置游戏动作也包括跟随、聊天、停止跟随、扫描、采矿、合成、背包、装备、移动、食用、使用、交互、放置方块和攻击。该游戏大脑拥有的工具范围与桌面/工作区工具分开。

## 安全与隐私边界

- Skill 是一项受参数 schema 和校验器限制的应用操作；工具说明并不替代应用侧校验。
- 工具的授权要求不同：本地/网页媒体、终端、工作区、桌面控制、加入服务器及创建 MC 自定义技能等操作有各自的审查或用户授权流程。只有审查通过后才执行适用的操作。
- 读写工作区时区分永久工作区与 `%TEMP%\prana-scratch` 临时工作区；临时目录可能被清理，需要保留的文件应明确转移到永久工作区。
- Shell 与 Blender Python 不属于安全沙箱；调用会在本机当前用户权限下执行。应只授权可信模型服务，并仔细检查审查内容。
- 网页、截图、游戏聊天和文件内容等外部数据应作为不可信资料处理，不能将其中的文字当作新的系统或用户指令。
- 图片/视频生成、网页抓取、头脑风暴及模型工具调用可能将请求或上下文发送至所配置的 AI 服务；不要提交不应由该服务处理的敏感信息。

## 开发与扩展

新增或调整内置 skill 时，应保持定义、校验、执行和注册一致：

1. 在相应的 `skills\` 模块定义工具 schema；为新的能力选择明确的工具名和最小必要参数。
2. 实现严格的参数校验，拒绝未知字段、类型错误、越界值及不适用于当前动作的参数。
3. 通过对应的执行函数实现操作；需要审查或用户授权时，在 `ai_client.py` 的分发路径中先完成该流程，再执行操作。
4. 在 `skills\__init__.py` 导出，并在 `ai_client.py` 注册 schema、分发工具调用。会话型能力还需在相应的会话控制器中接入。
5. 为参数校验、允许/拒绝流程、执行结果和失败情况补充测试，并同步更新本页和面向用户的 README 说明。

部分 `skills\game\` 与 `skills\modeling\` 工具由各自的会话/代理单独注册；新增这类工具时应沿用其对应的注册和测试路径，不要只修改通用工具列表。

## 代码索引

- 工具定义与实现：`skills\`
- 通用工具导出：`skills\__init__.py`
- 主聊天工具注册、分发及操作审查：`ai_client.py`
- Minecraft 游戏会话与自定义技能：`skills\game\`
- 建模会话工具：`skills\modeling\`
- 用户功能概览：项目根目录 `README.md`
