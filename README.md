# TSA Oh my god BA Prana

一个计划开发为**开源 AI 编码工具**的项目，目标是让开发者能够借助 AI 理解和处理代码项目。项目方向受到 [OpenCode](https://github.com/anomalyco/opencode) 等 AI 编码工具的启发；本项目将独立设计和实现。

## 项目愿景

希望让你的电脑拥有一位来自《蔚蓝档案》的普拉娜，作为陪伴你的桌面 AI 助手。她将逐步具备理解代码项目、回答开发问题和辅助编写代码等能力，并计划以 Windows 可执行程序（EXE）作为一种交付形式。


## 计划探索的能力

- 与 AI 对话，询问代码和开发问题。
- 帮助理解项目结构与现有代码。
- 辅助编写和修改代码。
- 根据实际需求逐步完善开发工作流与使用体验。

以上是项目方向，不代表这些功能已经实现或范围已经最终确定。

## 当前状态

目前提供一个可运行的 Windows 桌面聊天界面，并已接入兼容 OpenAI Chat Completions API 的模型服务。项目尚未实现代码库索引或自动修改代码。

**网页阅读：** 可在消息中粘贴最多 3 个公开 HTTP(S) 网页链接，程序会读取 HTML 网页正文并提取图片候选。每页最多读取 1 MB、向模型提供最多 12,000 个正文字符，并提取最多 20 个图片 URL。明确要求发送网页图片时，AI 可调用 `send_web_image` 从本轮候选中选择图片，下载并显示预览（单张最大 10 MB）；该技能不能读取候选列表之外的 URL。此功能按链接读取网页，不提供网页搜索，也不支持需要登录、JavaScript 渲染或非 HTML 的页面。对于 Bilibili 视频链接（`bilibili.com` 域名的 `/video/`、`/short-video/` 路径或 `?bvid=` 查询参数），程序会通过 Bilibili 公开 API 获取视频标题、UP 主、分 P、标签和简介，并附加封面图供模型参考；短链接（如 `b23.tv`）和 UP 主个人空间页不支持。

**附件分析：** “添加图片/视频”可发送 JPG、JPEG、PNG、GIF、WebP 图片或 MP4、MOV、AVI、MKV、WebM、M4V 视频给 AI 分析；单张图片最大 10 MB，单个视频最大 200 MB。视频在本机抽取最多 16 个均匀分布的画面帧，不包含声音或连续动作信息。模型和服务端须支持图像输入；主模型明确无法分析媒体时，程序会按配置顺序询问子代理能否直接查看，得到确认后再交给它分析。媒体分析子代理只为当前请求临时创建，不保留独立会话。

**媒体生成与展示：** 老师明确要求生成图片或视频时，AI 可调用 `send_image` 或 `send_video`。生成图片会显示预览，视频可用系统默认播放器打开，文件保存在 `%LOCALAPPDATA%\Prana\media`。视频生成目前请求 4 秒视频：普通 OpenAI 风格服务使用横向 1280×720，Agnes Video 2.5 使用 720P、16:9 文生视频参数。明确要求发送本机媒体时，`send_local_media` 会打开文件选择器；由老师选中的文件只在聊天中展示，不上传给 AI。

**模型与审查要求：** Skills 需要支持 OpenAI Chat Completions tool calling 的模型服务。媒体生成还需对应的图片、视频生成接口，包括 OpenAI 风格的 `POST /images/generations`，并可能产生额外费用。主模型调用媒体 skill 前，应用会将本轮请求和操作参数交给设置中的“操作审查模型”；只有明确批准才执行，未配置、拒绝或返回无效结果时均拒绝操作。审查模型共用主模型的 API 地址和 Key。用户主动添加的分析附件按原流程读取；媒体审查针对主模型发起的 skill 操作，不提供任意文件系统访问。媒体发送和生成 skills 仅在用户明确请求时调用。

**数据处理：** 配置的 API Key 通过 Windows DPAPI 加密后保存在当前用户的本机设置文件中。网页链接由本机直接访问，抓取的正文会连同消息及对话上下文发送到所选 AI 服务；请勿发送不应交给该服务处理的敏感内容。

### 编码 Agent 与双工作区

主模型可对编码任务（实现功能、修 bug、重构、写测试、运行验证）自主多步推进：用只读工具理解项目，用 `run_shell_command` 执行验证命令，用 `write_workspace_file` / `edit_workspace_file` 改代码，循环直至任务完成或遇到无法解决的错误。单条消息内工具调用受 `MAX_TOOL_ROUNDS = 20` 上限约束；可随时点“停止”中断。

**双工作区模型：**

- **永久工作区**：长期存在的目录。来源：(a) 应用工作目录（默认），(b) 老师在聊天中告知的路径（如“在 D:\myproject 工作”，AI 调 `switch_workspace` 切换，侧边栏会显示“已切换”并可“切回默认”）。Git 仅在永久区启用；scratch 下调用 `read_git_repository` 会报错。
- **临时工作区**：`%TEMP%\prana-scratch\`，程序启动时自动创建。AI 可自由读写，写操作仍须审查模型批准；产物如需持久化，须用 `transfer_workspace_file` 从临时区搬到永久区。

**新工具：**

- `run_shell_command`：在所选工作区执行一条 PowerShell 命令（用于 lint/test/build/验证）。`command` 与 `description` 必填，`timeout_seconds` 上限 300，`workdir` 可选（工作区相对子目录），`workspace` 可选（permanent/scratch，默认 permanent）。stdout/stderr 各截断到 16 KB；超时返回 `timed_out=true`。
- `transfer_workspace_file`：把文件从一个工作区复制到另一个（典型用于把 scratch 产物持久化到永久区）。目标已存在时须显式 `overwrite=true`。
- `switch_workspace`：把当前会话的激活永久工作区切换到老师告知的路径。路径须存在且为目录。

**审查要求：** 所有 workspace 写操作（write/edit/transfer）、shell 执行（run_shell_command）、工作区切换（switch_workspace）每次调用都必须通过“操作审查模型”批准；未配置审查模型、审查拒绝或返回无效结果时操作不会执行。这与现有终端、桌面控制、媒体生成的审查框架一致，沿用同一 `review_model` 设置字段，不新增独立配置。

**限制：** 危险命令（删除、格式化、`git push`、`iex` 等）由审查模型从严判断；程序层不做命令白名单。`run_shell_command` 在老师当前 Windows 账户权限下执行，请仅在信任当前 AI 服务时授权。

### 软件内引导与使用指南

- 新会话的欢迎页会根据配置状态显示“配置 AI”或“调整设置”，并提供“解释代码”“整理网页”“查看项目”三个提问示例。点击示例只会将文字追加到输入框，保留已有草稿；补充内容后再手动发送。
- 欢迎页和输入区下方的“使用指南”可打开离线帮助，按主题介绍快速开始、模型配置、聊天与会话、图片与视频、网页阅读、项目工具、桌面与 QQ 音乐、头脑风暴及常见问题。
- 设置窗口中的“配置说明”直接打开模型配置章节，说明主模型、媒体模型和操作审查模型的用途与共用 API 的关系。
- 输入区显示操作提示：**Enter 发送、Shift+Enter 换行、右键复制消息**；代码块可通过右键菜单单独复制。
- 使用指南支持主题切换、滚动阅读和 Esc 关闭；阅读指南不需要 API Key，也不会发起模型请求。

### QQ 音乐控制

老师明确要求控制桌面 QQ 音乐时，AI 可调用 `control_qq_music` skill 执行播放/暂停、上一首/下一首、停止、音量调节、静音、搜索或搜索并播放。该功能要求 QQ 音乐已在 Windows 上运行，应用不会自动启动它。

播放/暂停、切歌等操作发送 Windows 标准媒体键，音量键调节系统音量。搜索操作会定位 QQ 音乐主窗口的搜索框并输入关键词；`search` 仅展示搜索结果，`search_and_play`、`play` 或 `play_song` 会选择搜索建议并尝试播放，可通过 `result_index` 指定第 1–10 条建议（默认第 1 条）。程序会通过窗口标题检查搜索播放结果；无法确认目标歌曲开始播放时会明确报错。

### 桌面键鼠控制

老师明确要求查看屏幕或操作现有窗口时，AI 会先调用 `request_desktop_control`。程序弹出授权窗口，老师点“允许”后，再由“操作审查模型”审查用户请求和操作计划；两者都通过后，当前会话获得桌面控制授权。

授权后的能力：

- `get_info`、`list_windows`：读取虚拟屏幕范围、光标位置、前台窗口和可见窗口列表。
- `activate_window`：激活或从最小化恢复老师已经打开的窗口。
- `move`、`click`、`drag`、`scroll`：用真实鼠标事件操作，支持相对窗口客户区的坐标。
- `type`、`press`、`hotkey`：用真实键盘事件输入中文、英文、单键或快捷键。
- `screenshot`：截取前台窗口、指定窗口或全屏，缩放压缩后随本轮请求发送给模型。

限制与隐私：

- 第一版只复用老师现有窗口，不自动启动新程序。
- 授权只在当前会话内有效，不写入磁盘；清空对话、删除会话、退出程序或点“撤销”后失效。
- 长时间按住 Esc 1.5 秒可紧急停止；只按一下 Esc 不会触发。
- 截屏只在内存中交给模型，不保存截图文件；但截图可能包含密码、聊天记录等敏感信息，请只在信任当前 AI 服务时授权。
- 桌面控制需要主模型支持图像输入；不支持图像输入的模型无法分析截图。
- `Win+L`、`Ctrl+Alt+Delete`、`Win+R`、`Win+S/Q`、`Win+E` 等组合键被安全策略拦截。
- 第一版不提供隐藏桌面或虚拟机隔离；AI 的操作仍在老师当前 Windows 账户权限内。

### 多模型头脑风暴

主模型可以在回答复杂问题前自行调用 `start_brainstorm`，召集已配置的模型进行多轮讨论。参与者包括：

- 主模型自己
- 子代理配置中勾选“参与头脑风暴”的子代理
- 已链接提供商中勾选“参与头脑风暴”的模型

默认轮数为 10，可在“设置”中调整为 1-50。每位参与者每轮获得一次发言机会，可以跳过。讨论结束后，主模型只收到精简总结和关键分歧；完整记录保存在独立的头脑风暴子会话中，可在聊天中的“头脑风暴”卡片打开查看。

每位参与者拥有独立的只读子会话；老师不能在子会话中继续对话。主聊天和头脑风暴子会话都会持久化到 `%LOCALAPPDATA%\Prana\sessions`，重启后可以恢复；主聊天可通过顶部“会话”按钮新建、切换、重命名和删除。

头脑风暴会把当前请求和最近对话历史发送给所有参与者；请勿在讨论中包含不应交给这些服务处理的敏感内容。会话正文以明文保存在本机。API Key、子代理 Key 和链接提供商 Key 仍使用 Windows DPAPI 加密。

默认 10 轮会产生多次 API 调用，可能产生额外费用；只有主模型判断需要时才会发起，简单问题不会自动调用。参与者最多 12 位（含主模型）。

在“设置”中：

- “头脑风暴轮数”：设置讨论轮数。
- “配置子代理”：为子代理勾选“参与头脑风暴”。
- “管理链接提供商”：从 OpenCode 配置链接提供商，或从主模型 API 的 `GET /models` 获取模型列表；勾选参与讨论的模型。
- “从 OpenCode 导入”：继续用于填入主模型或添加子代理，不会自动修改 OpenCode 配置。

链接提供商的 API Key 会加密保存；`source=main` 的提供商使用当前主模型 API Key 和地址。缺少 Key 或地址的提供商会显示警告，并在参与前被跳过。不支持 OpenAI Chat Completions 接口的提供商可能无法参与。

### 配置 AI

主界面左侧默认显示内置提供商，未配置 API Key 时也可查看和选择：

| 提供商 | API 基础地址 |
| --- | --- |
| Agnes AI | `https://apihub.agnes-ai.com/v1` |
| OpenAI（GPT 官方） | `https://api.openai.com/v1` |

点击提供商会打开设置并预填 API 地址；填写该服务的 API Key 和对话模型，点击“保存”后生效。切换到不同提供商时，对话模型和操作审查模型需要重新填写，图片和视频模型预填对应服务的默认值。点击“取消”会保留原配置。侧边栏会标识当前使用的提供商，重启后根据已保存配置恢复；自定义服务仍可从右上角“设置”中配置。

点击界面右上角的“设置”即可配置 API Key、API 基础地址、对话模型、图片生成模型、视频生成模型和操作审查模型。首次使用须填写 API Key；已有 Key 且 API 地址未改变时，编辑框留空会保留原值，切换地址则必须填写对应的 Key。图片、视频和审查模型共用 API Key 与 API 地址；操作审查覆盖媒体、终端、工作区和桌面控制。暂不生成媒体时可保留图片、视频模型的默认值，实际生成时需填写当前服务支持的名称。各字段的详细用途可通过设置窗口中的“配置说明”查看。

设置保存在 `%LOCALAPPDATA%\Prana\settings.json`，其中 API Key 使用 Windows 当前用户的 DPAPI 加密；只有同一 Windows 用户可以解密。也可以使用下方表格列出的环境变量提供初始配置（仅在没有对应已保存设置时生效）。

使用 Agnes AI 图片生成时，可将 API 地址设置为 `https://apihub.agnes-ai.com/v1`，图片生成模型设置为 `agnes-image-2.0-flash`。图片生成请求会使用 `1024x1024` 尺寸。

使用 Agnes Video 2.5 时，将视频生成模型设置为 `agnes-video-2.5`，并使用 API 地址 `https://apihub.agnes-ai.com/v1`。程序会自动发送 `mode: "text"`，并按 Agnes 接口轮询任务和下载生成结果。

在“设置”中选择“配置子代理”可添加任意多个模型服务，为每个子代理填写名称、职责、API Key、API 地址和模型名称。发生媒体委派时，程序会依配置顺序询问子代理模型能否直接查看本轮媒体，得到确认后再交给该子代理分析；如果没有任何子代理确认支持，会明确提示。子代理仅在处理该次请求时存在，不创建独立会话或后台进程。

在“设置”中选择“从 OpenCode 导入”可读取 OpenCode 的全局配置文件 `%USERPROFILE%\.config\opencode\opencode.json`（也支持 `opencode.jsonc`；可用 `OPENCODE_CONFIG` 环境变量指定其他路径）和认证文件 `%USERPROFILE%\.local\share\opencode\auth.json`，列出其中 `provider` 段的提供商与模型。选中后可填入主模型设置，或预填到子代理编辑器。API Key 优先取自提供商的 `options.apiKey`（支持 `{env:变量名}` 占位符），否则读取认证文件中类型为 `api` 的 Key；OAuth 类型无法自动导入，需要在导入窗口中手动填写。导入只复制配置，不会修改 OpenCode 文件；保存后 Prana 仍会使用 DPAPI 加密 API Key。该功能面向 OpenAI Chat Completions 兼容接口，使用其他协议包的提供商可能无法正常工作，导入窗口会显示提示。

在“操作审查模型”中填写用于审核本地媒体、终端、工作区读取和桌面控制操作的模型名称。未配置审查模型时，主模型调用 `send_local_media`、`send_web_image`、`send_image` 或 `send_video` 会被拒绝；终端操作、工作区 skill 和桌面控制也会被拒绝。

用户明确要求检查项目内容时，AI 可通过 `run_terminal_command` 使用 PowerShell 或 CMD 风格命令查询应用工作目录。仅支持 `Get-Location`/`pwd`、`Get-ChildItem`/`dir`/`ls` 和 `Get-Content`/`type`/`cat` 等只读操作；读取小型文本文件，单文件最多 64 KB。每次执行前，审查模型都会收到用户请求和命令并必须明确批准。程序不启动操作系统 Shell，不支持脚本、命令链或写入操作；只有用户明确授权访问工作区外路径时，才允许读取用户已批准的目标文件或目录。

老师明确要求查看项目结构、按文件名或内容搜索项目文件，查看 Git 状态/日志/差异/分支，创建新文件，或对现有文件做局部修改时，AI 可分别调用 `get_project_structure`、`search_workspace_files`、`read_git_repository`、`write_workspace_file` 和 `edit_workspace_file`。前三个 skill 仅以只读方式访问应用工作目录，自动忽略常见缓存、构建和依赖目录，并对结果数量、扫描范围和输出长度设限；`write_workspace_file` 用于创建新文件或（在显式传入 `overwrite=true` 时）覆盖已有文件，`edit_workspace_file` 用于按精确字符串替换修改已有文件，两者都仅限应用工作目录内。每次调用前，审查模型都会收到用户请求和操作参数并必须明确批准。程序不提供任意文件系统访问或 Git 写操作。

当前工具的默认工作区由工具模块的位置确定，源码运行时为 Prana 项目根目录。更换启动命令所在目录，或在聊天中填写其他路径，都不会切换默认工作区；当前界面尚无工作区选择入口。

等待 AI 回复时仍可继续发送消息，请求会按顺序处理，每条用户消息都会得到对应回复。AI 可通过 `send_chat_message` skill 在一轮回复中主动发送多条短消息。等待期间可以点击“停止”取消未完成的请求；请求失败时会显示错误信息和“重试”按钮。消息支持右键复制，使用 ``` 围栏的代码块会以等宽字体显示。请求上下文过长时，程序会按约 32,000 字符自动裁剪较早的对话，仅影响发送给模型的内容，不删除界面中的完整历史。

| 环境变量 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `PRANA_API_KEY` | 否（可在界面设置） | 无 | AI 服务的 API Key |
| `PRANA_API_BASE_URL` | 否 | `https://api.openai.com/v1` | OpenAI 兼容 API 的基础地址 |
| `PRANA_MODEL` | 否 | `gpt-4o-mini` | 要使用的模型名称 |
| `PRANA_IMAGE_MODEL` | 否 | `gpt-image-1` | 图片生成模型名称 |
| `PRANA_VIDEO_MODEL` | 否 | `sora-2` | 视频生成模型名称 |
| `PRANA_REVIEW_MODEL` | 否 | 无 | 本地媒体、只读终端、工作区和桌面控制操作的审查模型名称 |

PowerShell 示例（仅对当前终端会话生效）：

```powershell
$env:PRANA_API_KEY = "你的 API Key"
$env:PRANA_API_BASE_URL = "https://api.openai.com/v1"
$env:PRANA_MODEL = "gpt-4o-mini"
$env:PRANA_IMAGE_MODEL = "gpt-image-1"
$env:PRANA_VIDEO_MODEL = "sora-2"
python main.py
```

也可以将 `PRANA_API_BASE_URL` 指向其他兼容 OpenAI Chat Completions API 的服务。请勿将真实 API Key 写入代码或提交到版本库。

### 会话与头脑风暴存储

- `%LOCALAPPDATA%\Prana\sessions\<会话 id>.json`：主会话、头脑风暴会话和参与者子会话。
- `%LOCALAPPDATA%\Prana\settings.json`：API Key（DPAPI 加密）、子代理、链接提供商、头脑风暴轮数。
- 删除主会话会一并删除其头脑风暴子会话；清空对话也会删除当前会话的子会话。

## 开发与运行

开发环境：Windows 和 Python 3.10+（安装时需包含 Tkinter）。

安装运行依赖：

```powershell
python -m pip install -r requirements.txt
```

直接启动：

```powershell
python main.py
```

构建独立 EXE：

```powershell
python -m pip install -r requirements-build.txt
.\build.cmd
```

生成的程序位于 `dist\Prana.exe`。EXE 构建需要 Windows 环境。EXE 同样支持界面内配置；环境变量可用于提供尚未保存的初始配置。

## 开源与许可

项目计划以开源方式开发，但目前尚未指定开源许可证。在许可证确定之前，请不要默认本项目内容可以自由复制、修改或分发。
