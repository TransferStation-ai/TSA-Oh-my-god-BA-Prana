# 开发与构建

## 环境要求

- Windows
- Python 3.10+，安装时需包含 Tkinter

安装运行依赖并启动应用：

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 源码热更新

开发模式会检查 `skills\` 下的 Python 源文件。检测到已注册 skill 的实现或工具定义修改后，会在下一次 AI 请求开始时重载已加载的技能模块及工具定义，无需重启应用。语法错误会阻止本次重载并明确报错；修正源码后可再次请求。

新增/删除 skill、修改 skill 会话类、热更新器或 `ai_client.py` 分发逻辑仍需重启。打包后的 EXE 不启用源码热更新。

技能目录、注册流程和测试建议见 [Skills 说明](skills.md#开发与扩展)。

## 构建独立 EXE

```powershell
python -m pip install -r requirements-build.txt
.\build.cmd
```

生成的程序位于 `dist\Prana.exe`。EXE 构建需要 Windows 环境；EXE 同样支持界面内配置，环境变量可提供尚未保存的初始配置。
