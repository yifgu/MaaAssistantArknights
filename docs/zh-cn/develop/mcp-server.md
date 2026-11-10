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

`mcp_platform` 默认为 `all`，可选 `win-x64` 或 `linux-x64` 只构建所需平台，避免重复占用 runner。

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

Windows Debug 与 MaaFramework 的 Release ControlUnit ABI 不兼容，因此 Debug 包不包含该可选 DLL，并拒绝 `--touch-mode MaaFwAdb`；使用默认 `maatouch`、`adb` 或 `minitouch`，或改用完整的 `RelWithDebInfo` 包。

## 本地测试

无需设备即可运行离线单元测试、真实 MCP stdio 握手和模拟情报服务测试：

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

实际 Debug Core 的任务注册、参数拒绝、回调和地图投影检查也不需要设备。合成画面测试通过本地模拟传输代替 ADB 驱动真实 Core，覆盖暂停识别、取消、时间戳顺序、PNG 对应关系和重放：

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest \
  src/MaaMcpServer/tests/test_native.py src/MaaMcpServer/tests/test_battle_control.py -q
```

PowerShell 先设置 `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`，再运行同一条 pytest 命令。资源不在安装目录时，另设 `MAA_MCP_RESOURCE_DIR` 为其父目录。未设环境变量时，这项测试会明确跳过，不代表实机验证通过。

Windows 源码 Debug 构建也需要先运行 `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install`，运行时要求与上面的 Debug 产物一致。

worker 使用独立子进程和专用 stdin/stdout 管道，不继承 MCP 的输入输出流。stderr 会记录进程 PID 及 `worker_started`、`core_load`、`resource_load`、`core_create`、`ready` 和请求阶段；启动或连接超时会报告最后阶段，不会误称战斗结果未知。超时或取消仍会终止 worker、释放控制锁；战斗命令中断仍按 `outcome_unknown` 处理，不自动重试。

`battle_status` 和 `battle_connect` 分别返回 `project_version`、`build`（包内 `BUILD-INFO.json`）与 `resources`（配置的资源根目录和地图索引 SHA-256）；缺少构建清单或索引时对应值为 null，不使用 MCP SDK 版本冒充项目版本。连接成功后另有 `core_version`。绑定前会按唯一 stage/level ID 检查地图索引及文件，拒绝显示编号（如 `TL-EX-1`）；缺图会明确指出请求 ID 和资源根目录，不会自动下载或回退绑定。绑定结果的 `map_source` 另含地图文件 SHA-256；这些信息不代表画面已验证或跨版本兼容。

可选实机冒烟测试需显式设置 `MAA_MCP_NATIVE_CORE`、`MAA_MCP_LIVE_ADDRESS` 和 `MAA_MCP_LIVE_STAGE`，再运行 `uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_stdio.py -k real_device`。资源、ADB 和连接配置可分别通过 `MAA_MCP_RESOURCE_DIR`、`MAA_MCP_ADB_PATH`、`MAA_MCP_CONNECTION_CONFIG` 指定。先停止其他控制器，手动进入与唯一 ID 对应的固定地图战斗；测试只连接、绑定、截图和关闭，不恢复、部署或退出战斗。未设置条件会明确跳过；模拟 worker 的离线通过不等于 Windows 实机通过。

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

新会话接入手动暂停的战斗时，可在 `battle_identify` 中传入 `request.adopt_manual_pause=true`，明确授权接管而不恢复战斗。该参数为严格布尔值，默认 false；省略时仍拒绝未归本服务管理的暂停。Core 首先要求画面被识别为战斗且未显示运行中的暂停按钮，无法识别的画面不能接管。识别只在暂停时查看卡牌，即使 OCR 不完整或操作被取消也不会主动恢复。识别完成后返回 `pause_verified`、`safe_state`、最终同帧的 `snapshot` / `image_path` 及 PNG 内容，部分识别同样返回；截图保存失败通过 `evidence_error` 明示，取消或 worker 故障可能导致无法返回最终证据。冷启动不再需要外部 ADB 点击。

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

需要执行单个战术动作时，优先使用 `battle_step`，将预检、必要的恢复、动作、动作后截图和暂停验证放在同一 Core 命令中：

```json
{
  "request": {
    "session_id": "replace-with-session-id",
    "operation_id": "step-001",
    "action": "skill",
    "unit_id": "replace-with-observed-unit-id",
    "adopt_manual_pause": false,
    "timeout_ms": 10000
  }
}
```

`action` 可为 `deploy`、`retreat` 或 `skill`；部署还须提供 `location` 和 `direction`。手动暂停默认不接管；只有明确设置 `adopt_manual_pause=true` 并由新截图确认暂停后才会恢复。每次动作至多执行一次，工具先取得动作边界截图，再请求暂停并返回新截图与 `pause_verified`；取消客户端请求时服务会等待该命令完成清理。若 worker 本身失效，结果会明确报告暂停状态未知。`battle_step` 的 `timeout_ms` 上限为 10000。

`captured_at_ms` 是取帧完成后、分析开始前记录的主机 Unix 毫秒时间，不是设备渲染时间。`capture_time_snapshot` 始终对应 `capture_image_path`（动作边界），`return_time_snapshot` 对应 `return_image_path`（稍后的暂停验证）；MCP 图片内容仍按返回帧、动作边界帧排列。`observation_id` 按分析顺序递增而非取帧顺序，因此动作边界观测的 ID 可能更大，但时间戳更早。系统时钟调整会影响时间差，这些时间戳不构成运行时长硬限制。完全相同的操作重放保留原始时间戳与路径，新截图则记录自己的取帧时间。

`battle_identify` 返回 `identified_count`、`unresolved_count`、`ambiguous_count` 和逐卡 `identity_reason`；空名缓存不会再抑制详情 OCR。其他动作返回 `outcome_unknown` 时，MCP `isError` 为 true，即使 `helper_returned` 或 `action_returned` 为 true 也不代表游戏内成功。须截图复核，不能盲目重试。场上位置标为 `action_history_unverified`，敌人状态为 null；首版不接受模型直接覆写状态。单独的暂停请求不等于已确认暂停，恢复只允许针对本服务请求过或明确接管的暂停。

普通命令的 `timeout_ms` 为 1–30000；`battle_step` 最多 10000。普通命令超时或取消会终止独立 worker、使会话失效，并保存未知结果；step 取消会等待已准备的暂停清理，worker 失效时仍可能无法确认状态。`battle_close` 可中断普通命令；遇到 step 时会等待其暂停清理后关闭，但本身不负责暂停或退出游戏。通过 `battle_get_operation` 查询持久结果；日志及 `operations.sqlite3` 位于 `--user-dir`，每场最多保留最近 128 张主动截图。旧场次、日志和数据库需要自行定期清理。

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

动作名为 `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused`、`step`。`step` 一次只接受一个已预检的战术动作，并在动作后立即取帧、请求暂停、验证并返回暂停帧；它不能保证设备底层 I/O 或整个 MCP 进程故障时一定暂停。更新参数只入队，设备操作均在 Core 任务线程执行。原生调用者仍须实现外部 watchdog 和去重；单独使用 C ABI 时，Core 不保证底层 I/O 在期限内返回。MCP 服务实现了进程级 watchdog。自动试验和优化器留待后续实现。

`identify` 接受可选的严格布尔参数 `args.adopt_manual_pause`，默认 false。显式接管只取得已识别暂停的控制权，不切换暂停，也不授权此次识别恢复战斗。
