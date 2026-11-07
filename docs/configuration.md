# 模型与配置

应用设置可通过右上角“设置”打开。设置窗口中的“配置说明”也会显示主模型、媒体模型和操作审查模型的用途。

## 服务商与模型

内置服务商的连接方式如下：

| 服务商 | 连接方式 |
| --- | --- |
| Agnes AI | `https://apihub.agnes-ai.com/v1` |
| OpenAI（GPT 官方） | `https://api.openai.com/v1` |
| kimi（kimi 官方） | `https://api.moonshot.cn/v1` |
| GitHub Copilot | GitHub 官方 Copilot SDK/CLI（无需 API 地址或 API Key） |

点击侧栏服务商可打开设置并预填连接信息。设置顶部菜单可以切换内置服务、已保存的链接/自定义服务或“自定义地址…”。API Key 按服务商分别保存；切换后 Key 留空即复用该服务商已保存的凭据。对话模型可从已保存或实时获取的目录中选择，也可手动输入。刷新 OpenAI 兼容服务的模型列表会请求 `GET /models`；Copilot 则通过 SDK 获取可用模型。失败时保留原目录，不影响手动切换。切换服务商只影响后续消息，不更改已有会话历史。

### GitHub Copilot

GitHub Copilot 通过 [GitHub 官方 Copilot SDK](https://github.com/github/copilot-sdk) 调用 Copilot CLI 运行时，需要可使用 Copilot 的 GitHub 账号/订阅。首次使用前请在终端登录 Copilot CLI，或登录已安装的 GitHub CLI；SDK 会复用本机登录凭据，不要求也不会在 Prana 设置中保存 API Key。首次启动 SDK 运行时可能需要联网下载组件。模型可选 `auto` 自动路由，也可在设置中刷新并选择当前账号可用的模型。

Copilot 的内置 CLI 文件/终端工具不会开放给 Prana。模型请求的 Prana Skills 仍由应用原有的执行与操作审查流程处理。Copilot SDK 模式目前不转发图片/视频附件，也不支持图片或视频生成；这些能力需切换到支持对应媒体接口的提供商。GitHub Copilot 订阅与已退役的 GitHub Models API 是不同服务。

图片生成模型、视频生成模型和操作审查模型按服务商分别保存；目标服务没有审查模型时会清空该项，敏感操作继续 fail-closed。图片或视频模型留空时，对应功能会提示尚未配置。“取消”会保留原设置。

图片、视频和审查请求共用当前主模型的 API 地址与 Key。操作审查模型用于审核媒体、终端、工作区、桌面控制和加入 Minecraft 服务器等操作；没有配置、审查拒绝或返回无效结果时，相关敏感操作不会执行。

使用 Agnes AI 图片生成时，可将 API 地址设为 `https://apihub.agnes-ai.com/v1`，图片生成模型设为 `agnes-image-2.0-flash`；请求尺寸为 `1024x1024`。使用 Agnes Video 2.5 时，将视频生成模型设为 `agnes-video-2.5` 并使用相同 API 地址；应用会发送 `mode: "text"`，按 Agnes 接口轮询任务并下载结果。

OpenAI 兼容服务需支持 Chat Completions tool calling 才能使用 Skills；Copilot 使用官方 SDK 的自定义工具接口。媒体生成功能还需要相应的图片/视频生成接口；图片生成使用 OpenAI 风格 `POST /images/generations`，调用可能产生额外费用。桌面截图和图片/视频分析还需要模型服务支持图像输入。

## 子代理与链接提供商

在“配置子代理”中可添加任意多个模型服务，为每个子代理设置名称、职责、API Key、API 地址和模型名称。媒体委派时，应用按配置顺序询问子代理能否直接查看本轮媒体，获得确认后才交给该子代理分析；没有子代理确认支持时会明确提示。子代理只为该次请求临时创建，不建立独立会话或后台进程。

“管理链接提供商”可从 OpenCode 配置链接提供商，或从主模型 API 的 `GET /models` 获取模型列表，并选择参与头脑风暴的模型。`source=main` 的提供商使用当前主模型 API Key 和地址。缺少 Key 或地址的提供商会显示警告，并在参与前跳过；不支持 OpenAI Chat Completions 接口的服务可能无法参与。链接提供商的 API Key 会加密保存。

“从 OpenCode 导入”可读取全局配置 `%USERPROFILE%\.config\opencode\opencode.json`（也支持 `.jsonc`；`OPENCODE_CONFIG` 可指定其他路径）和认证文件 `%USERPROFILE%\.local\share\opencode\auth.json`，列出 `provider` 段的服务商和模型。API Key 优先读取 `options.apiKey`（支持 `{env:变量名}`），否则读取认证文件中类型为 `api` 的 Key；OAuth 类型需在导入窗口手动填写。导入只复制配置，不修改 OpenCode 文件。功能面向 OpenAI Chat Completions 兼容接口，使用其他协议的服务可能无法工作。

## 环境变量

可用以下环境变量提供初始配置；仅在没有对应已保存设置时生效。

| 环境变量 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `PRANA_API_KEY` | 否（可在界面设置） | 无 | OpenAI 兼容 AI 服务 API Key；Copilot 使用本机 CLI 登录，不读取此项 |
| `PRANA_API_BASE_URL` | 否 | `https://api.openai.com/v1` | OpenAI 兼容 API 基础地址 |
| `PRANA_MODEL` | 否 | `gpt-4o-mini` | 对话模型名称 |
| `PRANA_IMAGE_MODEL` | 否 | `gpt-image-1` | 图片生成模型名称 |
| `PRANA_VIDEO_MODEL` | 否 | `sora-2` | 视频生成模型名称 |
| `PRANA_REVIEW_MODEL` | 否 | 无 | 操作审查模型名称 |

PowerShell 示例（仅对当前终端会话生效）：

```powershell
$env:PRANA_API_KEY = "你的 API Key"
$env:PRANA_API_BASE_URL = "https://api.openai.com/v1"
$env:PRANA_MODEL = "gpt-4o-mini"
$env:PRANA_IMAGE_MODEL = "gpt-image-1"
$env:PRANA_VIDEO_MODEL = "sora-2"
python main.py
```

也可将 `PRANA_API_BASE_URL` 指向其他兼容 OpenAI Chat Completions API 的服务。不要将真实 API Key 写入代码或提交到版本库。

## 本地数据与隐私

- `%LOCALAPPDATA%\Prana\settings.json` 保存设置。API Key、子代理 Key 和链接提供商 Key 使用 Windows 当前用户的 DPAPI 加密，只有同一 Windows 用户可以解密。
- Copilot 登录凭据由 GitHub Copilot CLI/GitHub CLI 管理，不保存在 Prana 设置文件中。
- `%LOCALAPPDATA%\Prana\sessions\<会话 id>.json` 保存主会话、头脑风暴会话和参与者子会话；会话正文以明文保存在本机。删除主会话会一并删除其头脑风暴子会话；清空对话也会删除当前会话的子会话。
- 游戏技能保存在 `%LOCALAPPDATA%\Prana\game_skills`；图片和视频生成文件保存在 `%LOCALAPPDATA%\Prana\media`。
- 网页链接由本机直接访问；抓取的正文会连同消息和对话上下文发送至所选 AI 服务。头脑风暴也会将当前请求和近期对话发送给所有参与模型。请勿发送不应交由这些服务处理的敏感信息。

更多操作授权和数据边界见[使用指南](user-guide.md)与[编码 Agent 与工作区](coding-agent.md)。
