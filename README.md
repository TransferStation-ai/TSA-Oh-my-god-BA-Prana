# TSA Oh my god BA Prana

一个计划开发为开源 AI 编码工具的 Windows 桌面项目。Prana 希望成为一位桌面 AI 助手，帮助开发者理解和处理代码项目；项目方向受到 [OpenCode](https://github.com/anomalyco/opencode) 等工具启发，但将独立设计和实现。

目前提供可运行的桌面聊天界面，并接入兼容 OpenAI Chat Completions API 的模型服务。项目仍在开发中，功能和范围可能变化。


## 文档

| 文档 | 内容 |
| --- | --- |
| [使用指南](docs/user-guide.md) | 聊天、网页与媒体、QQ 音乐、Minecraft、桌面控制和头脑风暴 |
| [编码 Agent 与工作区](docs/coding-agent.md) | 项目工具、Shell、Git、永久/临时工作区和操作审查 |
| [模型与配置](docs/configuration.md) | 服务商、模型、环境变量、子代理、OpenCode 导入和本地数据 |
| [Skills 说明](docs/skills.md) | 内置工具目录、触发方式、安全边界和扩展方法 |
| [开发与构建](docs/development.md) | 安装依赖、启动、源码热更新和 EXE 构建 |

项目计划以开源方式开发，但目前尚未指定开源许可证。在许可证确定之前，请不要默认项目内容可以自由复制、修改或分发。

## Garry's Mod 玩家模型

Prana 可以按老师的要求，把参考模型（YSM JSON + 贴图）导入 Blender，分步建立身体与手部几何，渲染多角度预览，并编译成 Garry's Mod 可加载的玩家模型 addon。

### 前置工具

- Blender 3.0（必填）：在“设置 → 建模工具路径”填写 `blender.exe` 的完整路径。
- Blockbench（可选）：仅用于打开参考模型，不参与最终建模与导出。
- Garry's Mod 根目录（可选）：用于查找 `studiomdl`；SFM 根目录可作为 Source 工具回退。

### 设置字段

| 字段 | 说明 |
| --- | --- |
| Blender 可执行文件路径 | 必填，例如 `D:\mc\blender-3.0.1-windows-x64\blender.exe` |
| Blockbench 可执行文件路径 | 可选，仅作参考查看 |
| Garry's Mod 根目录 | 可选，例如 `D:\Steam\steamapps\common\GarrysMod` |
| SFM 根目录 | 可选，作为 Source 工具回退 |

### 典型流程

1. 明确说明要制作的玩家模型，并提供 YSM 参考模型目录与输出名（仅小写字母、数字、下划线）。
2. AI 调用 `start_modeling_job` 创建作业，按需用 `open_modeling_reference` 查看参考。
3. 通过 `run_blender_python` 小步建模，并用 `render_modeling_preview` 查看正面、背面、侧面和三分之四视图。
4. 确认外观后调用 `compile_gmod_model`，程序会编译身体与第一人称手模并生成 lua 注册文件。
5. 老师自己安装：把 `addon\<名称>\` 复制到 `garrysmod/addons`，重启游戏后选择该玩家模型。

### 限制

- 程序化建模质量有限，复杂造型需要多轮调整；动作、ragdoll、材质和第一人称手模需要人工验收。
- 身体与手模分别注册，程序不会自动安装或修改老师已有的 `addons`，也不会上传到创意工坊。
- 预览图、模型内容、脚本输出和日志均按不可信数据处理。
- 作业目录位于 `%LOCALAPPDATA%\Prana\modeling\`，包含 `source.json`、`work.blend`、`renders\`、`scripts\`、`export\` 和 `addon\`。
