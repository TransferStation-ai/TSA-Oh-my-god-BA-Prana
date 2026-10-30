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

目前提供一个可运行的 Windows 桌面聊天界面，并已接入兼容 OpenAI Chat Completions API 的模型服务。可在消息中粘贴最多 3 个公开 HTTP(S) 网页链接，程序会读取 HTML 网页正文并提取网页图片候选；每个网页最多读取 1 MB、最多向模型提供 12,000 个正文字符，并提取最多 20 个图片 URL。用户明确要求发送网页图片时，AI 可调用 `send_web_image` 从这些候选中选择图片，程序会下载并在对话中显示预览（单张最大 10 MB）；该技能不能读取候选列表之外的 URL。此功能按链接读取网页，不提供网页搜索，也不支持需要登录、JavaScript 渲染或非 HTML 的页面。可通过“添加图片/视频”发送 JPG、PNG、GIF、WebP 图片或 MP4、MOV、AVI、MKV、WebM、M4V 视频（图片最大 10 MB，视频最大 200 MB）给 AI 分析。视频会在本机抽取最多 16 个均匀分布的画面帧，再随消息发送；分析不包含声音或连续动作信息。AI 也可按老师的明确要求调用 `send_image` 和 `send_video` media skills 生成并发送媒体：生成的图片会显示预览，视频可用系统默认播放器打开，文件保存在 `%LOCALAPPDATA%\Prana\media`。明确要求发送本机媒体时，AI 可调用 `send_local_media` skill，应用会打开文件选择器供老师选择图片或视频；选中的文件只在聊天中展示，不会上传给 AI。主模型尝试调用会读取或写入本地媒体的 skill 前，应用会将本轮用户请求和操作参数交给设置中指定的文件访问审查模型；仅审查模型明确批准时才执行，审查模型未配置、拒绝或返回无效结果时均拒绝该操作。该审查模型使用与主模型相同的 API 地址和 API Key。用户主动添加的附件仍按原有流程读取；审查覆盖的是主模型发起的媒体 skill 操作，不提供任意文件系统访问。视频生成目前请求 4 秒视频；普通 OpenAI 风格服务使用横向 1280×720，Agnes Video 2.5 使用 720P、16:9 文生视频参数。Skills 需要兼容 OpenAI Chat Completions tool calling 的模型服务；媒体生成还需要支持 OpenAI 风格的 `POST /images/generations` 与视频生成接口，并且可能产生额外 API 费用。媒体发送/生成 skills 仅在用户明确请求时调用。模型和服务端必须支持图像输入；可在“设置”中配置多个子代理，主模型明确表示无法分析媒体时，程序会按配置顺序询问子代理模型能否直接查看该媒体，得到确认后再将媒体交给它分析。子代理只为当前请求临时创建，不保留会话；配置的 API Key 会通过 Windows DPAPI 加密后保存在当前用户的本机设置文件中。消息中的网页链接会由本机直接访问，抓取的网页正文会连同消息和对话上下文传到所选 AI 服务；请勿发送不应交给该服务处理的敏感内容。项目尚未实现代码库索引或自动修改代码。

老师明确要求控制桌面 QQ 音乐时，AI 可调用 `control_qq_music` skill 切换播放/暂停或搜索歌曲、歌手和专辑。该功能要求 QQ 音乐已在 Windows 上运行；播放/暂停发送 Windows 标准媒体播放/暂停按键，搜索会在 QQ 音乐窗口中使用 `Ctrl+F` 并提交关键词，不会自动播放搜索结果。应用不会启动 QQ 音乐。

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

点击界面右上角的“设置”即可配置 API Key、API 地址、对话模型、图片生成模型、视频生成模型和文件访问审查模型。图片、视频和审查模型共用 API Key 与 API 地址。设置保存在 `%LOCALAPPDATA%\Prana\settings.json`，其中 API Key 使用 Windows 当前用户的 DPAPI 加密；只有同一 Windows 用户可以解密。也可以使用以下环境变量提供初始配置（仅在没有对应已保存设置时生效）：

使用 Agnes AI 图片生成时，可将 API 地址设置为 `https://apihub.agnes-ai.com/v1`，图片生成模型设置为 `agnes-image-2.0-flash`。图片生成请求会使用 `1024x1024` 尺寸。

使用 Agnes Video 2.5 时，将视频生成模型设置为 `agnes-video-2.5`，并使用 API 地址 `https://apihub.agnes-ai.com/v1`。程序会自动发送 `mode: "text"`，并按 Agnes 接口轮询任务和下载生成结果。

在“设置”中选择“配置子代理”可添加任意多个模型服务，为每个子代理填写名称、职责、API Key、API 地址和模型名称。发生媒体委派时，程序会依配置顺序询问子代理模型能否直接查看本轮媒体，得到确认后再交给该子代理分析；如果没有任何子代理确认支持，会明确提示。子代理仅在处理该次请求时存在，不创建独立会话或后台进程。

在“设置”中选择“从 OpenCode 导入”可读取 OpenCode 的全局配置文件 `%USERPROFILE%\.config\opencode\opencode.json`（也支持 `opencode.jsonc`；可用 `OPENCODE_CONFIG` 环境变量指定其他路径）和认证文件 `%USERPROFILE%\.local\share\opencode\auth.json`，列出其中 `provider` 段的提供商与模型。选中后可填入主模型设置，或预填到子代理编辑器。API Key 优先取自提供商的 `options.apiKey`（支持 `{env:变量名}` 占位符），否则读取认证文件中类型为 `api` 的 Key；OAuth 类型无法自动导入，需要在导入窗口中手动填写。导入只复制配置，不会修改 OpenCode 文件；保存后 Prana 仍会使用 DPAPI 加密 API Key。该功能面向 OpenAI Chat Completions 兼容接口，使用其他协议包的提供商可能无法正常工作，导入窗口会显示提示。

在“操作审查模型”中填写用于审核本地媒体、终端和工作区读取操作的模型名称。未配置审查模型时，主模型调用 `send_local_media`、`send_web_image`、`send_image` 或 `send_video` 会被拒绝；终端操作和工作区 skill 也会被拒绝。

用户明确要求检查项目内容时，AI 可通过 `run_terminal_command` 使用 PowerShell 或 CMD 风格命令查询应用工作目录。仅支持 `Get-Location`/`pwd`、`Get-ChildItem`/`dir`/`ls` 和 `Get-Content`/`type`/`cat` 等只读操作；读取仅限 UTF-8 文本文件，单文件最多 64 KB。每次执行前，审查模型都会收到用户请求和命令并必须明确批准。程序不启动操作系统 Shell，不支持脚本、命令链或写入操作；只有用户明确授权访问工作区外路径时，才允许读取用户已批准的目标文件或目录。

老师明确要求查看项目结构、按文件名或内容搜索项目文件，或查看 Git 状态/日志/差异/分支时，AI 可分别调用 `get_project_structure`、`search_workspace_files` 和 `read_git_repository`。这些工作区 skill 仅以只读方式访问应用工作目录，自动忽略常见缓存、构建和依赖目录，并对结果数量、扫描范围和输出长度设限；每次调用前，审查模型都会收到用户请求和操作参数并必须明确批准。程序不提供任意文件系统访问、文件写入或 Git 写操作。

等待 AI 回复时仍可继续发送消息，请求会按顺序处理，每条用户消息都会得到对应回复。AI 可通过 `send_chat_message` skill 在一轮回复中主动发送多条短消息。等待期间可以点击“停止”取消未完成的请求；请求失败时会显示错误信息和“重试”按钮。消息支持右键复制，使用 ``` 围栏的代码块会以等宽字体显示。请求上下文过长时，程序会按约 32,000 字符自动裁剪较早的对话，仅影响发送给模型的内容，不删除界面中的完整历史。

| 环境变量 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `PRANA_API_KEY` | 否（可在界面设置） | 无 | AI 服务的 API Key |
| `PRANA_API_BASE_URL` | 否 | `https://api.openai.com/v1` | OpenAI 兼容 API 的基础地址 |
| `PRANA_MODEL` | 否 | `gpt-4o-mini` | 要使用的模型名称 |
| `PRANA_IMAGE_MODEL` | 否 | `gpt-image-1` | 图片生成模型名称 |
| `PRANA_VIDEO_MODEL` | 否 | `sora-2` | 视频生成模型名称 |
| `PRANA_REVIEW_MODEL` | 否 | 无 | 本地媒体、只读终端和工作区操作的审查模型名称 |

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
