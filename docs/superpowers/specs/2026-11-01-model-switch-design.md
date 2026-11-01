# 模型自由切换设计（opencode 式体验）

日期：2026-11-01
状态：草稿（建造者检查前）

## 意图与背景

Prana 目前的主模型切换体验：

- 切换提供商 = 在设置对话框里改"API 地址"，且**地址变了就必须重填 API Key**（`main.py: _open_settings` 的 `save()`：空 key + 地址变化 → `raise ValueError("切换 API 地址后，请填写该提供商的 API Key。")`）。这是用户明确的痛点。
- 主模型名 = 手填文本，没有可选目录。
- 每个提供商的 Key 只有一份全局 `api_key`（`settings.json`），换提供商就把上家的 Key 顶掉了。

目标（对标 opencode 仓库 https://github.com/anomalyco/opencode/ 的体验）：

1. **填 Key 只发生一次**：每个提供商的凭据持久加密保存，之后切回任意提供商直接可用。
2. **模型目录**：每个提供商有可选的模型列表；主模型以"提供商 + 模型"保存，切换时从目录里挑，不用手打。
3. **切换只影响后续消息**，会话历史保留（现有行为天然满足，只需确认）。

## 用户批准的范围

- 包含：① 按提供商的 Key 记忆；② 模型目录；④ 切换会话语义（只影响后续消息）。
- 不含：侧边栏新增"模型"区块（砍掉；切换入口 = 现有设置对话框，右上角"设置"按钮 + 侧边栏提供商按钮都打开它）。
- 不做：子代理管理流程改动、linked providers 的 brainstorm 开关语义改动、新提供商的动态 onboarding（目录之外的提供商通过"自定义地址"兜底）。

## 现状（可复用的件）

| 件 | 位置 | 说明 |
|---|---|---|
| `BUILTIN_PROVIDERS`（3 个 preset） | `providers.py` | Agnes AI / OpenAI GPT官方 / kimi官方；**后两者 base_url 相同**（`https://api.openai.com/v1`）——按 base_url 建 Key 库会撞车，Key 库必须以 preset 为粒度 |
| Key 加密 | `settings_store.py` | Windows `CryptProtectData`（同 Windows 用户会话内可解密），base64 包裹；`api_key`、子代理 Key、linked provider Key 全部已走此路 |
| linked providers | `settings.json → linked_providers` | 每条已含 `base_url`、加密 `api_key`、`models[{id,name,brainstorm}]`、`compatible/disabled`；"填入主模型"（`_open_opencode_import`）已能把 Key+地址+模型自动填进主设置字段——**Key 只填一次**在这条路上基本已实现 |
| `/models` 拉取 | `ai_client.py: fetch_main_provider_models()` | GET `{base_url}/models`（Bearer），返回 `[{id,name}]`，上限 200；现在只为主提供商拉 |
| 双 Listbox 提供商/模型选择器 | `_open_linked_providers`、`_open_opencode_import` | 现成的"左提供商、右模型"交互模式 |
| 配置持久化 | `ai_client.py: save_configuration(...)` | 校验 key/地址/模型非空 → `settings_store.save_settings` 加密落盘 + 更新内存 |

## 设计

### 数据：`settings.json` 新增 `provider_vault`

```jsonc
{
  // 现有字段全部保留（api_key/base_url/model 仍是"当前主提供商"的活值，
  // ai_client 继续以它们工作 → 向后兼容、无需迁移）
  "provider_vault": {
    "preset:Agnes AI": {
      "name": "Agnes AI",
      "base_url": "https://apihub.agnes-ai.com/v1",
      "api_key_enc": "<base64 CryptProtectData>",
      "api_key_hint": "sk-…后4位",
      "models": [{"id": "agnes-3.0-flash", "name": "Agnes 3.0 Flash"}],
      "image_model": "agnes-image-2.0-flash",
      "video_model": "agnes-video-2.5",
      "review_model": "",
      "updated_at": "2026-11-01T12:00:00"
    },
    "linked:<opencode id>": { "...": "..." },
    "custom:<normalized base_url>": { "...": "..." }
  }
}
```

- **Vault key（provider_key）= 提供商粒度**，解决 OpenAI 与 kimi 共享 base_url 的撞车：
  - 内置 preset：`preset:<名称>`（同名两个 preset 各存各的 Key）
  - 来自 opencode 链接的：`linked:<id>`（id 稳定）
  - 用户手填的自定义地址：`custom:<normalize_base_url(地址)>`
- 旧 settings.json 没有该字段 → 按"空库"读；保存时**自动迁移**：把当前 `api_key/base_url/model` 写成一个 vault 条目（匹配内置 preset 则 `preset:`，否则 `custom:`）。只读旧文件永不报错。
- linked provider 的 Key/模型在 `linked_providers` 里已有一份。切它为主时把条目**快照**进 vault（`linked:<id>`），此后 vault 条目是活记录；"刷新模型"更新 vault 条目。不双向同步（避免两处真相）；`linked_providers` 里的 brainstorm 开关继续归它管。

### 每提供商的媒体/审查模型

- `image_model / video_model / review_model` 存进 vault 条目。
- 内置 preset 首次使用：填 preset 默认（沿用现有 `switching_provider` 时的预填行为）。
- linked/custom：默认留空。
- 切换主提供商时：取目标 vault 条目的值；**审查模型特殊处理**——审查请求固定发往主提供商端点（`_review_sensitive_operation` 用主 Key/地址 + review_model），若目标条目没有保存的 review_model，则**置空 = 回退为自审**（现状空 review_model 的既有行为），不沿用旧提供商的模型名（旧名字在新端点大概率 404）。
- 媒体模型名同理：切提供商后旧的 image/video 模型名属于旧提供商，采用目标条目里保存的值（没有就是空，媒体生成时提示未配置）。

### 模型目录的来源（优先级）

1. vault 条目里已保存的 `models`（linked 导入时自带；preset/custom 由"刷新模型"填充）
2. 实时拉取：用目标提供商的 base_url + Key 调 `/models`（复用 `fetch_main_provider_models` 的逻辑，参数化为任意 base_url/key）
3. 手填兜底：目录永远允许手动输入模型名（Combobox 可编辑 / 文本框）

拉取失败（401、无 `/models` 端点、代理）→ 提示错误，保留已保存列表，不阻塞切换。

### 设置对话框改造（`_open_settings`）

现有字段（API Key / API 地址 / 主模型 / 图像模型 / 视频模型 / 审查模型 / 头脑风暴轮次）之上，**新增顶部"提供商"下拉**（`tkinter.OptionMenu`；项目未用 ttk）：

- 列表 = 3 个内置 preset + 所有带 Key 的 vault 条目（`linked:`/`custom:`）+ "自定义地址…"
- 选择某提供商后联动：
  - API 地址：填该条目 base_url（preset/linked 只读；选"自定义地址…"时可编辑）
  - API Key：从 vault 预填（脱敏显示前缀/后缀，如 `sk-…abcd`；**字段留空或不变 = 保留该条目里的 Key；重新输入 = 覆盖**）。这样"切换提供商"永远不用填 Key——Key 只在那家提供商第一次配置时填一次。
  - 主模型：变成交互式选择——双 Listbox（左该提供商目录、右……）或单 Listbox + 手动输入框；沿用 `_open_linked_providers` 的既有模式（构建者自选，见下）
  - 图像/视频/审查模型：按上节规则预填
- "刷新模型列表"小按钮：线程拉取目标提供商 `/models`，upsert 进 vault 条目的 `models`，刷新目录。
- `save()`：
  - 校验：地址 + 模型非空；Key 按"留空保留"规则从 vault 取
  - 写 vault 条目（upsert）+ 现有 `save_configuration`（当前主提供商的活值）
  - 现有"切换 API 地址后必须填 Key"的报错**删除**——地址变了就从 vault 取该地址的 Key；vault 没有且字段也为空 → 提示"该提供商还没有保存 Key，请填写"（此时才需要填，且只此一次）

### 侧边栏（`_build_provider_sidebar`）

- 保持 3 个 preset 按钮不变。
- 状态标签逻辑：当前 `base_url` 匹配哪个 preset 就高亮；非 preset 提供商（linked/custom）时全部不高亮，可加一行小字"自定义提供商"（构建者细定）。

### 会话语义

`save_configuration` 更新内存后立即生效；聊天会话的既有条目不动，后续消息用新提供商+模型。无需额外工作，测试里加一条确认即可。

## 涉及模块

| 模块 | 改动 |
|---|---|
| `providers.py` | 不变或加 provider_key 构造辅助 |
| `settings_store.py` | `provider_vault` 的 load/save/加密（沿用 CryptProtectData 封装）+ 旧文件兼容 |
| `ai_client.py` | `fetch_main_provider_models` 参数化为任意 base_url/key；`save_configuration` 接受 vault 条目并 upsert；`_review_sensitive_operation` 切提供商后 review_model 置空的逻辑 |
| `main.py` | `_open_settings` 加提供商下拉 + 联动预填 + 目录选择 + 刷新按钮；`_open_linked_providers` 的"刷新模型"改为写 vault 条目 |
| `tests/` | settings_store 往返 + 旧文件兼容；vault 迁移；目录拉取参数化；切换后 review 回退；sidebar 状态 |

## 约束

- Python 3.14 / 纯 tkinter（无 ttk）/ PyInstaller 构建（不得引入新依赖）
- `CryptProtectData` 只在原 Windows 用户下可解密——这是现状，linked/vault 都受同样限制，行为不劣化
- `settings.json` 新字段必须对旧文件可选

## 留给建造者的决定（标注：这些是我的默认选择，可按工程判断调整，但不改变要构建的东西）

- 模型目录的控件形态：单 Listbox（目录）+ 可编辑输入框 vs 双 Listbox；我倾向 Listbox + 可编辑输入框（"手动输入"是常态而非例外）
- "自定义地址…"项的处理：复用地址输入框还是独立小对话框
- vault 条目的 `api_key_hint` 生成规则（前 4 后 4 vs 后 4）
- 非 preset 提供商时侧边栏的展示文案
- 刷新模型失败时的提示方式（messagebox vs 内联 Label）
