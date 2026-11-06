---
icon: mdi:robot
order: 20
---

# 战斗 MCP Server

`src/MaaMcpServer` 提供本地 stdio MCP 服务，通过独立进程加载 MaaCore。需要 Python 3.11+、uv 和本分支编译的 Core；旧版本没有 `BattleControl` 任务。该功能是实验性的，不是危机合约自动优化器。

## 编译与安装

以下命令从仓库根目录执行。先按 [Linux 编译教程](./linux-tutorial.md) 或对应平台教程准备编译器和 CMake；Windows 在 Visual Studio 开发者终端运行。

```bash
git submodule update --init --recursive src/MaaUtils
python tools/maadeps-download.py
cmake --preset linux-x64 -B build-mcp -DBUILD_WPF_GUI=OFF -DINSTALL_RESOURCE=ON
cmake --build build-mcp --config Debug --target MaaCore -j 2
cmake --install build-mcp --config Debug --prefix "$PWD/build-mcp/install"
uv sync --project src/MaaMcpServer
```

Windows x64 将配置命令的 `linux-x64` 换为 `windows-x64`；macOS 根据架构使用 `macos-arm64` 或 `macos-x64`。不要混用不同平台的构建目录。安装目录应同时包含 Core、运行时依赖和 `resource/`；`--resource-dir` 可单独指定包含 `resource/` 的父目录。

## 使用 GitHub Actions 产物

在自己的仓库中打开 **Actions → Release Pipeline → Run workflow**，选择包含 MCP 改动的分支，勾选 `mcp_only`，并选择 `mcp_configuration`：

- `RelWithDebInfo`：默认的完整运行包，无需本地安装 Python、uv、ADB 或 Visual Studio。
- `Debug`：开发测试包。Linux 可直接自测；Windows 需本机提供不可再分发的 Debug CRT。

两种配置都会先编译 Debug Core 并运行完整测试，再打包所选配置；不编译 GUI，也不发布 Release。命令行等价操作：

```bash
gh workflow run ci.yml --ref <branch> -f mcp_only=true -f mcp_configuration=RelWithDebInfo
```

相关 PR 的 Release Pipeline 也会运行同样的 `Battle MCP` 作业。实现位于 `.github/workflows/mcp-build.yml`（**Battle MCP Bundle**），正式流水线和 Nightly 共用它；新工作流尚未进入默认分支时，仍可通过已有的 Release Pipeline 手动触发。手动 MCP 构建使用独立并发组，不会被同分支的 PR 构建取消。

成功后，在该次运行的 **Artifacts** 下载 `MAAMcp-win-x64-RelWithDebInfo` 或 `MAAMcp-linux-x64-RelWithDebInfo`（Debug 配置则对应 `-Debug`），再解压其中的同名 `.zip` 或 `.tar.gz`。包内包含 `core/`（Core、原生依赖、资源和 ADB ControlUnit）、`python/`（独立 Python 及锁定的全部 Python 运行/测试依赖）、`adb/`、启动脚本、源码与测试、wheel/sdist、依赖许可证及 `BUILD-INFO.json`（实际源码提交和构建链接）。旁附 SHA-256 校验文件；Windows 运行包还包含可再分发的 VC++ CRT。无需克隆仓库、重新编译或联网安装 Python 包。Linux 仍需要兼容的 x64 系统与系统 glibc；这不是容器或完整操作系统。

Windows x64 从下载目录执行：

```powershell
Expand-Archive .\MAAMcp-win-x64-RelWithDebInfo.zip .\mcp-test
Set-Location .\mcp-test
.\run-mcp.cmd --check
.\run-mcp.cmd --address 127.0.0.1:5555
```

Linux x64 从下载目录执行：

```bash
mkdir mcp-test
tar -xzf MAAMcp-linux-x64-RelWithDebInfo.tar.gz -C mcp-test
cd mcp-test
./run-mcp.sh --check
./run-mcp.sh --address 127.0.0.1:5555
```

`--check` 验证内置 ADB、Python 依赖与 Core/资源加载，不连接设备。正常启动是 stdio 服务，会等待 MCP 输入，不提供网页。配置客户端时无需 uv：Windows 的 `command` 使用包内 `python/python.exe`，Linux 使用 `python/bin/python3`；`args` 为 `["-I","-B","/absolute/mcp-test/run_mcp.py","--address","127.0.0.1:5555"]`。所有路径使用绝对路径。默认状态目录是包内 `state/`，可用 `--user-dir` 指定其他可写目录。

完整离线测试使用 Debug 包的 `run-mcp.cmd --self-test` 或 `./run-mcp.sh --self-test`，不连接设备。Windows Debug 包需先安装 Visual Studio 2026/Build Tools 的 C++ 桌面工作负载与 Windows SDK，再执行 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/PrepareMcpRuntime.ps1 -CoreDir core`。**Debug CRT 不可再分发，普通 VC++ Redistributable 无法替代它**；脚本只复制本机开发工具的 DLL，修改后的目录不可再分发。CI 在独立 Debug 目录中完成测试，产物不会包含 Debug CRT。`RelWithDebInfo` 包拒绝 `--self-test`，避免 Release 断言关闭产生误导。

实机操作仍须按下文连接并核对地图。不要混用旧 Core、资源或其他提交的测试。外部情报服务是可选插件，其程序与游戏数据快照不属于 MCP 运行依赖，须单独配置；包不会捆绑游戏或模拟器，也不自动调用云端模型。

## 本地测试

无需设备即可运行离线单元测试、真实 MCP stdio 握手和模拟情报服务测试：

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

实际 Debug Core 的任务注册、参数拒绝、回调和地图投影检查也不需要设备：

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_native.py -q
```

PowerShell 先设置 `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`，再运行同一条 pytest 命令。资源不在安装目录时，另设 `MAA_MCP_RESOURCE_DIR` 为其父目录。未设环境变量时，这项测试会明确跳过，不代表实机验证通过。

Windows 源码 Debug 构建也需要先运行 `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install`，运行时要求与上面的 Debug 产物一致。

## 启动与客户端配置

先关闭其他控制同一设备的 MAA/GUI 任务，手动进入普通固定地图战斗。首版不支持自动选词条、进入关卡、识别结算、多阶段镜头或中途恢复已有干员身份。

同一个 `--user-dir` 有跨进程控制锁。请为同一设备始终使用同一个目录；该锁无法阻止使用其他目录的服务或外部 GUI 操作设备。

召唤物和装置的部署会明确拒绝；在实现实例级身份跟踪前，不能把多个同名单位当成一个干员操作。

```bash
uv run --project src/MaaMcpServer maa-battle-mcp \
  --core-dir "$PWD/build-mcp/install" \
  --adb-path /absolute/path/to/adb \
  --address 127.0.0.1:5555 \
  --user-dir "$PWD/build-mcp/session"
```

该命令等待 MCP 客户端输入，不会输出网页或自动连接设备。客户端配置示例，路径须替换为绝对路径；Windows 的 JSON 路径使用 `/` 或转义反斜杠：

```json
{
  "mcpServers": {
    "maa-battle": {
      "command": "uv",
      "args": [
        "run", "--project", "/absolute/repo/src/MaaMcpServer",
        "maa-battle-mcp", "--core-dir", "/absolute/repo/build-mcp/install",
        "--adb-path", "/absolute/path/to/adb", "--address", "127.0.0.1:5555",
        "--user-dir", "/absolute/repo/build-mcp/session"
      ]
    }
  }
}
```

按顺序调用 `battle_connect`、`battle_attach(stage_name="main_01-07")`、`battle_capture_evidence` 和 `battle_observe`。关卡 ID 必须对应实际画面；地图绑定来自调用者，不是自动识别。`battle_get_map` 返回静态格子与两种屏幕投影。未知名字返回 null，需要主动识别时调用 `battle_identify`；它可能点击详情并请求暂停，之后须查看截图并显式调用 `battle_set_paused(paused=false)` 恢复。

部署示例，替换会话和单位 ID；工具参数包含一层 `request`：

```json
{
  "request": {
    "session_id": "replace-with-session-id",
    "operation_id": "deploy-001",
    "unit_id": "replace-with-observed-unit-id",
    "location": [4, 3],
    "direction": "Left",
    "timeout_ms": 10000
  }
}
```

`battle_retreat`、`battle_use_skill` 使用同样的请求，但不传 `location` 和 `direction`。每次新操作使用新 `operation_id`；完全相同的请求重发只读取已保存结果，同 ID 改参数会拒绝。费用不足或冷却未结束直接失败，不会无限等待。截图工具返回同一帧的 PNG 和观测，供客户端多模态模型判断，不自动调用云端模型。

动作返回 `outcome_unknown` 时，MCP `isError` 为 true，即使 `helper_returned` 为 true 也不代表游戏内成功。须截图复核，不能盲目重试。场上位置标为 `action_history_unverified`，敌人状态为 null；首版不接受模型直接覆写状态。暂停请求也不等于已确认暂停，恢复只允许针对本服务请求过的暂停。

`timeout_ms` 为 1–30000；超时或取消会终止独立 worker、使会话失效，并保存未知结果。`battle_close` 可中断执行，但**不会暂停或退出游戏**。通过 `battle_get_operation` 查询持久结果；日志及 `operations.sqlite3` 位于 `--user-dir`，每场最多保留最近 128 张主动截图。旧场次、日志和数据库需要自行定期清理。

## 可选情报服务

先单独安装并准备情报服务的数据快照，再使用 `--intel-config /absolute/intel.json`。配置是受信任的本地启动配置，不能由模型提交任意程序：

```json
{
  "provider": "intelligence",
  "command": "uv",
  "args": ["run", "--project", "/absolute/arknights-mcp", "arknights-mcp", "serve", "--transport", "stdio"],
  "region": "cn"
}
```

PRTS 可使用 `{"provider":"prts","command":"prts-mcp","region":"cn"}`；适配器强制 stdio 和结构化输出请求。`intel_query` 支持 `stage/operator/enemy`，Intelligence 使用游戏唯一 ID，PRTS 使用关卡 ID 或干员/敌人中文名称。Intelligence 的 `routes_page`、`spawns_page` 从 1 开始，每页 50 条；返回保留来源、限制说明及分页信息，缓存 5 分钟。PRTS 关卡查询只返回敌人，不提供地图、路线或波次，也不自动混用外服数据。上游数据不是实时战况，本服务不自动合并或校准外部地图。

## Core 协议

初始化 `AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})` 后调用 `AsstStart`，等待 `SubTaskExtraInfo` 的 `what="BattleControlReady"`（协议版本 1）。之后使用 `AsstSetTaskParams` 发送 `session_id`、`command_id`、`action`、`args`、`timeout_ms`，等待 `what="BattleControlResult"`。每次只允许一个命令；读取回调前不要继续投递。

动作名为 `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused`。更新参数只入队，设备操作均在 Core 任务线程执行。原生调用者仍须实现外部 watchdog 和去重；单独使用 C ABI 时，Core 不保证底层 I/O 在期限内返回。MCP 服务实现了该进程级 watchdog。识别后置确认、自动试验和优化器留待后续实现。
