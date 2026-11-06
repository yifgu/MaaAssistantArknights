---
icon: mdi:robot
order: 20
---

# 戰鬥 MCP Server

`src/MaaMcpServer` 提供本機 stdio MCP 服務，透過獨立程序載入 MaaCore。需要 Python 3.11+、uv 和本分支編譯的 Core；舊版本沒有 `BattleControl` 任務。此功能仍屬實驗性質，不是危機合約自動最佳化工具。

## 編譯與安裝

以下命令從儲存庫根目錄執行。先依 [Linux 編譯教學](./linux-tutorial.md) 或對應平台教學準備編譯器和 CMake；Windows 使用 Visual Studio 開發者終端機。

```bash
git submodule update --init --recursive src/MaaUtils
python tools/maadeps-download.py
cmake --preset linux-x64 -B build-mcp -DBUILD_WPF_GUI=OFF -DINSTALL_RESOURCE=ON
cmake --build build-mcp --config Debug --target MaaCore -j 2
cmake --install build-mcp --config Debug --prefix "$PWD/build-mcp/install"
uv sync --project src/MaaMcpServer
```

Windows x64 將 `linux-x64` 換成 `windows-x64`；macOS 使用 `macos-arm64` 或 `macos-x64`。不同平台不要共用建置目錄。安裝目錄須同時包含 Core、執行期相依項目和 `resource/`；`--resource-dir` 可另外指定包含 `resource/` 的父目錄。

## 使用 GitHub Actions 產物

在自己的儲存庫開啟 **Actions → Release Pipeline → Run workflow**，選擇 MCP 分支，勾選 `mcp_only` 並選擇 `mcp_configuration`：

- `RelWithDebInfo`：預設的完整執行套件，不需另裝 Python、uv、ADB 或 Visual Studio。
- `Debug`：開發測試套件。Linux 可直接自測；Windows 須由本機提供不可再散布的 Debug CRT。

兩者都會先編譯 Debug Core 並執行完整測試，再打包所選設定；不編譯 GUI，也不發布 Release。等效命令：

```bash
gh workflow run ci.yml --ref <branch> -f mcp_only=true -f mcp_configuration=RelWithDebInfo
```

相關 PR 也會執行相同的 `Battle MCP` 作業。`.github/workflows/mcp-build.yml`（**Battle MCP Bundle**）由正式與 Nightly 流水線共用；尚未進入預設分支時可由既有的 Release Pipeline 手動觸發。手動 MCP 建置使用獨立並行群組，不會被同分支的 PR 建置取消。

成功後從 **Artifacts** 下載 `MAAMcp-win-x64-RelWithDebInfo` 或 `MAAMcp-linux-x64-RelWithDebInfo`（Debug 設定則為 `-Debug`），再解壓同名 `.zip` 或 `.tar.gz`。內容包括 `core/`（Core、原生相依項目、資源與 ADB ControlUnit）、`python/`（獨立 Python 與鎖定的全部執行/測試相依套件）、`adb/`、啟動腳本、原始碼與測試、wheel/sdist、相依項目授權和 `BUILD-INFO.json`（原始碼提交與建置連結），另附 SHA-256。Windows 執行套件包含可再散布的 VC++ CRT。不需複製儲存庫、重新編譯或連網安裝 Python 套件。Linux 仍需相容的 x64 系統與系統 glibc；此套件不是容器或完整作業系統。

Windows x64 從下載目錄執行：

```powershell
Expand-Archive .\MAAMcp-win-x64-RelWithDebInfo.zip .\mcp-test
Set-Location .\mcp-test
.\run-mcp.cmd --check
.\run-mcp.cmd --address 127.0.0.1:5555
```

Linux x64 從下載目錄執行：

```bash
mkdir mcp-test
tar -xzf MAAMcp-linux-x64-RelWithDebInfo.tar.gz -C mcp-test
cd mcp-test
./run-mcp.sh --check
./run-mcp.sh --address 127.0.0.1:5555
```

`--check` 驗證內建 ADB、Python 相依項目與 Core/資源載入，不連接裝置。正常啟動是 stdio 服務，會等待 MCP 輸入，沒有網頁。用戶端的 `command` 使用包內 `python/python.exe`（Windows）或 `python/bin/python3`（Linux），`args` 為 `["-I","-B","/absolute/mcp-test/run_mcp.py","--address","127.0.0.1:5555"]`；所有路徑使用絕對路徑。狀態預設儲存於包內 `state/`，可用 `--user-dir` 指定其他可寫目錄。

完整離線測試使用 Debug 包的 `run-mcp.cmd --self-test` 或 `./run-mcp.sh --self-test`，不連接裝置。Windows 須先安裝 Visual Studio 2026/Build Tools 的 C++ 桌面工作負載與 Windows SDK，再執行 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/PrepareMcpRuntime.ps1 -CoreDir core`。**Debug CRT 不可再散布，一般 VC++ Redistributable 無法替代它**；腳本只複製本機開發 DLL，修改後的目錄不可再散布。CI 在獨立 Debug 目錄測試，產物不含 Debug CRT。`RelWithDebInfo` 包會拒絕 `--self-test`，避免斷言停用造成誤導。

實機操作仍須依下文連接並核對地圖。不要混用舊 Core、資源或其他提交的測試。外部情報程式與資料快照是選用外掛，不屬於伺服器執行相依項目，須另行設定；套件不包含遊戲、模擬器，也不自動呼叫雲端模型。

Windows Debug 與 MaaFramework Release ControlUnit 的 ABI 不相容，因此 Debug 包不包含此選用 DLL，並拒絕 `--touch-mode MaaFwAdb`。請用預設 `maatouch`、`adb` 或 `minitouch`，或改用完整 `RelWithDebInfo` 包。

## 本機測試

不需要裝置即可執行離線單元測試、實際 MCP stdio 握手與模擬情報服務測試：

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

實際 Debug Core 的任務註冊、參數拒絕、回呼與地圖投影檢查也不需要裝置：

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_native.py -q
```

PowerShell 先設定 `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`，再執行同一條 pytest 命令。資源不在安裝目錄時，另設 `MAA_MCP_RESOURCE_DIR` 為其父目錄。未設定環境變數時會明確跳過此測試；離線通過不代表實機行為已確認。

Windows 原始碼 Debug 建置也須先執行 `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install`，執行期需求與上述 Debug 產物相同。

## 啟動與用戶端設定

先關閉其他控制同一裝置的 MAA/GUI 任務，手動進入固定地圖戰鬥。首版不支援自動選詞條、進入關卡、識別結算、多階段鏡頭或重建接管前已部署的幹員。

同一個 `--user-dir` 有跨程序控制鎖。同一裝置請固定使用同一目錄；此鎖無法阻止使用其他目錄的服務或外部 GUI 操作裝置。

召喚物與裝置的部署會明確拒絕；實作個體身分追蹤前，不能將多個同名單位當成一位幹員操作。

```bash
uv run --project src/MaaMcpServer maa-battle-mcp \
  --core-dir "$PWD/build-mcp/install" \
  --adb-path /absolute/path/to/adb \
  --address 127.0.0.1:5555 \
  --user-dir "$PWD/build-mcp/session"
```

此命令等待 MCP 用戶端輸入，不會提供網頁或自動連線。設定內請替換為絕對路徑；Windows JSON 路徑使用 `/` 或跳脫反斜線：

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

依序呼叫 `battle_connect`、`battle_attach(stage_name="main_01-07")`、`battle_capture_evidence` 和 `battle_observe`。關卡 ID 必須符合實際畫面；地圖綁定由呼叫者提供，不是自動識別。`battle_get_map` 回傳靜態格子與兩種螢幕投影。未知名字回傳 null。需要主動識別時呼叫 `battle_identify`，它可能點開詳情並請求暫停；之後檢查截圖，再明確呼叫 `battle_set_paused(paused=false)` 恢復。

部署範例，請替換會話和單位 ID；工具參數有一層 `request`：

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

`battle_retreat`、`battle_use_skill` 使用同樣請求，但不傳 `location` 和 `direction`。每次新操作使用新 `operation_id`；完全相同的請求重送只讀取保存結果，同 ID 改參數會拒絕。費用不足或冷卻中直接失敗，不會無限等待。截圖工具回傳同一影格的 PNG 和觀測供用戶端多模態模型判斷，不會自動呼叫雲端模型。

`outcome_unknown` 會設定 MCP `isError=true`，即使 `helper_returned=true` 也不代表遊戲內成功。必須截圖確認，不可盲目重試。場上位置標為 `action_history_unverified`，敵人狀態為 null；首版不接受模型直接覆寫狀態。請求暫停不代表已確認暫停，恢復僅允許針對本服務請求過的暫停。

`timeout_ms` 為 1–30000；逾時或取消會終止 worker、使會話失效並保存未知結果。`battle_close` 可以中斷控制，但**不會暫停或退出遊戲**。`battle_get_operation` 可查詢持久結果。日誌與 `operations.sqlite3` 位於 `--user-dir`；每場保留最近 128 張主動截圖。舊場次、日誌和資料庫需自行定期清理。

## 可選情報服務

先另外安裝情報服務並準備資料快照，再使用 `--intel-config /absolute/intel.json`。此設定是受信任的本機啟動設定，不能讓模型提交任意程式：

```json
{
  "provider": "intelligence",
  "command": "uv",
  "args": ["run", "--project", "/absolute/arknights-mcp", "arknights-mcp", "serve", "--transport", "stdio"],
  "region": "cn"
}
```

PRTS 可使用 `{"provider":"prts","command":"prts-mcp","region":"cn"}`；配接器會要求 stdio 與結構化輸出。`intel_query` 支援 `stage/operator/enemy`：Intelligence 使用遊戲唯一 ID，PRTS 使用關卡 ID 或幹員／敵人簡體中文名稱。Intelligence 的 `routes_page`、`spawns_page` 從 1 開始，每頁 50 筆。結果保留來源、限制與分頁資訊，快取 5 分鐘。PRTS 關卡查詢只回傳敵人，不含地圖、路線或波次，也不會暗中混用其他區服資料。情報不是即時戰況；本服務不自動合併或校準外部地圖。

## Core 協議

初始化 `AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})` 後呼叫 `AsstStart`，等待 `SubTaskExtraInfo` 的 `what="BattleControlReady"`（協議版本 1）。之後透過 `AsstSetTaskParams` 傳入 `session_id`、`command_id`、`action`、`args`、`timeout_ms`，等待 `what="BattleControlResult"`。每次只允許一個命令，收到回呼後才可繼續投遞。

動作名稱為 `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused`。更新參數只加入佇列，裝置操作均在 Core 任務執行緒執行。原生呼叫者仍須自行實作外部 watchdog 與去重；單獨使用 C ABI 無法保證底層 I/O 在期限內返回，MCP 服務提供程序層級 watchdog。自動確認後置條件、反覆試驗與最佳化工具留待後續實作。
