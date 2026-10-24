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

目前提供一个可运行的 Windows 桌面聊天界面，并已接入兼容 OpenAI Chat Completions API 的模型服务。可在消息中粘贴最多 3 个公开 HTTP(S) 网页链接，程序会读取 HTML 网页正文并与问题一起发送给 AI；每个网页最多读取 1 MB，并最多向模型提供 12,000 个正文字符。此功能按链接读取网页，不提供网页搜索，也不支持需要登录、JavaScript 渲染或非 HTML 的页面。可通过“添加图片/视频”发送 JPG、PNG、GIF、WebP 图片或 MP4、MOV、AVI、MKV、WebM、M4V 视频（图片最大 10 MB，视频最大 200 MB）。图片直接作为图像输入，视频会在本机抽取最多 16 个均匀分布的画面帧，再随消息发送。模型和服务端必须支持图像输入；视频分析基于抽取的画面，不包含声音或连续动作信息。可在“设置”中配置多个图片/视频子代理；主模型明确表示无法分析媒体时，程序会按配置顺序选择支持相应媒体类型的子代理。子代理只为当前请求临时创建，不保留会话；配置的 API Key 会通过 Windows DPAPI 加密后保存在当前用户的本机设置文件中。消息中的网页链接会由本机直接访问，抓取的网页正文会连同消息和对话上下文传到所选 AI 服务；请勿发送不应交给该服务处理的敏感内容。项目尚未实现代码库索引或自动修改代码。

### 配置 AI

点击界面右上角的“设置”即可配置 API Key、API 地址和模型名称。设置保存在 `%LOCALAPPDATA%\Prana\settings.json`，其中 API Key 使用 Windows 当前用户的 DPAPI 加密；只有同一 Windows 用户可以解密。也可以使用以下环境变量提供初始配置（仅在没有对应已保存设置时生效）：

在“设置”中选择“配置子代理”可添加任意多个模型服务，为每个子代理填写名称、职责、API Key、API 地址和模型名称，并勾选其支持的媒体类型。发生媒体委派时，程序依配置顺序选取第一个匹配的子代理；如果没有匹配项，会明确提示配置缺失。子代理仅在处理该次请求时存在，不创建独立会话或后台进程。

| 环境变量 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `PRANA_API_KEY` | 否（可在界面设置） | 无 | AI 服务的 API Key |
| `PRANA_API_BASE_URL` | 否 | `https://api.openai.com/v1` | OpenAI 兼容 API 的基础地址 |
| `PRANA_MODEL` | 否 | `gpt-4o-mini` | 要使用的模型名称 |

PowerShell 示例（仅对当前终端会话生效）：

```powershell
$env:PRANA_API_KEY = "你的 API Key"
$env:PRANA_API_BASE_URL = "https://api.openai.com/v1"
$env:PRANA_MODEL = "gpt-4o-mini"
python main.py
```

也可以将 `PRANA_API_BASE_URL` 指向其他兼容 OpenAI Chat Completions API 的服务。请勿将真实 API Key 写入代码或提交到版本库。

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
