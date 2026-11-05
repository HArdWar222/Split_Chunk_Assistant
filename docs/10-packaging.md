# 打包与安装（Windows）

本文说明如何把工作台打成 **Windows 目录版（onedir）+ zip**，以及如何用 Inno Setup 生成安装包。

> 编辑页面仍在浏览器里：exe 启动后监听 `127.0.0.1:8790`，再自动打开默认浏览器。  
> 安装包不会把网页变成桌面窗口应用。

## 1. 先决条件

- Windows 10/11 64 位
- Python 3.10+（打包机需要；目标机器不需要）
- `pip install pyinstaller`（`build.ps1` 会尝试自动安装）
- 可选：Inno Setup 6（只有生成安装包时需要）

## 2. 打目录版

```powershell
cd <仓库根>
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

产物：

```
dist\
  SplitChunkAssistant\          # 目录版
    SplitChunkAssistant.exe
    _internal\                  # 运行时、web/、vendor/splitchunk/
  SplitChunkAssistant-win64.zip # 可直接发给别人解压运行
```

参数：`-NoZip` 跳过 zip；`-SkipInstall` 不自动安装 PyInstaller。

## 3. 生成安装包（可选）

1. 安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。
2. 先用上面的命令打出 `dist\SplitChunkAssistant\`。
3. 编译脚本：

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
```

产物：`dist\SplitChunkAssistant-Setup.exe`。

安装行为：

- 安装到 `C:\Program Files\SplitChunkAssistant`（需要管理员）
- 创建开始菜单/桌面快捷方式与卸载项
- 数据**不打进**安装包；首次启动时创建

## 4. 配置与数据放在哪

| 场景 | `config.json` / `data\` 位置 |
| --- | --- |
| exe 所在目录可写（例如解压到桌面、`D:\Tools`） | exe 同级 |
| exe 在 `Program Files` 等不可写目录 | `%LOCALAPPDATA%\SplitChunkAssistant\` |

`data\` 结构见 [使用说明（傻瓜版）](09-user-guide.md) 第 11 节：`source/`、`chunks/`、`draft/`、`backup/`。

迁移数据：把旧 `data\` 整个拷到新位置，必要时改 `config.json` 里的 `data_root`（支持绝对路径）。

## 5. 打包内容与升级

`_internal\` 中关键内容：

- `web\`：`index.html` / `app.js` / `style.css`
- `vendor\splitchunk\`：SPLITCHUNK 引擎（含 `profile_data/`）

**升级切块引擎**：替换 `_internal\vendor\splitchunk\chunking_v2` 即可，不必重新打包；替换后重启 exe，`/api/config` 的 `engine.version` 会显示新版本。

**升级工作台**：重新跑 `build.ps1` / 重新安装。

## 6. 常见问题

**SmartScreen / 杀毒误报**

- 未签名的 exe 首次运行可能提示「Windows 已保护你的电脑」→「更多信息」→「仍要运行」。
- 推荐目录版而不是单文件版：目录版启动快、误报少，子进程（切块引擎）也稳定。
- 正式分发建议购买代码签名证书给 exe 和安装包签名。

**双击后没反应**

- 目录版是控制台程序：启动后会有一个命令行窗口（显示端口、数据目录、引擎版本）。
- 若窗口一闪而过，通常是配置或数据目录权限问题：在命令行里运行 exe 看错误信息。

**端口被占用**

- 默认 `8790`。换端口：`SplitChunkAssistant.exe --port 8801`；或用 `--no-open` 不自动开浏览器。

**防火墙提示**

- 服务只绑定 `127.0.0.1`，不需要放行公网；若弹窗可选择允许「专用网络」或直接取消（本机仍可用）。

**数据放哪、怎么备份**

- 见第 4 节；定期复制 `data\` 即可。

**许可**

- 内置的 SPLITCHUNK 引擎上游声明 `license = "Proprietary"`；对外分发安装包前请确认授权范围（详见 `vendor/splitchunk/UPSTREAM.md`）。

## 7. 版本号与发布

- 安装包版本在 `packaging/installer.iss` 的 `MyAppVersion`。
- 应用内版本在 `chunk_studio/__init__.py`（`__version__` / `__phase__`）。
- 每次发布：改版本号 → `build.ps1` → 编译安装包 → 在干净机器上按第 8 节冒烟。

## 8. 发布前冒烟清单

在**打包产物**（不是源码）上验证：

1. 双击 exe → 浏览器打开页面，左栏有文件。
2. 设置弹层能打开、`data_root` 显示正确。
3. 往 `data\source\` 丢一个 `.md` → 3 秒内自动切块，chip 提示完成。
4. 点文件 → 原文与切片联动、对齐状态正常。
5. 编辑一次 → 保存草稿 → 提交 → `data\backup\` 出现备份。
6. 左栏「⬆ 添加文件」导入一个 md，确认自动切块。
7. 关闭命令行窗口 → 服务停止；重新双击可再次使用。
