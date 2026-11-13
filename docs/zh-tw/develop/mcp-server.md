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

`dev-v2` 上符合既有建置路徑的推送（包括 PR 合併）會自動執行 **Battle MCP Bundle**，預設建置 Windows/Linux x64 的 `RelWithDebInfo` 完整套件，不再自動啟動全量 Release Pipeline。純文件變更不觸發 MCP 建置；其它文件與驗證工作流程、PR 全量建置、`v*` 標籤發布和手動全量建置維持原有觸發規則。

需要手動建置時，在自己的儲存庫開啟 **Actions → Battle MCP Bundle → Run workflow**，選擇 MCP 分支並選擇 `configuration`：

- `RelWithDebInfo`：預設的完整執行套件，不需另裝 Python、uv、ADB 或 Visual Studio。
- `Debug`：開發測試套件。Linux 可直接自測；Windows 須由本機提供不可再散布的 Debug CRT。

兩者都會先編譯 Debug Core 並執行完整測試，再打包所選設定；不編譯 GUI，也不發布 Release。等效命令：

`platform` 預設為 `all`，可選 `win-x64` 或 `linux-x64` 只建置所需平台，避免重複占用 runner。

```bash
gh workflow run mcp-build.yml --ref <branch> -f configuration=RelWithDebInfo -f platform=all
```

相關 PR 的 Release Pipeline 也會執行相同的 `Battle MCP` 作業。`.github/workflows/mcp-build.yml` 仍由正式與 Nightly 流水線共用。原有的 **Release Pipeline → Run workflow** 入口仍可使用：勾選 `mcp_only`，透過 `mcp_configuration` 和 `mcp_platform` 選擇設定與平台；不勾選則執行全量建置。手動 MCP 建置不會被同分支的 PR 建置取消。

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

實際 Debug Core 的任務註冊、參數拒絕、回呼與地圖投影檢查也不需要裝置。合成畫面測試以本機模擬傳輸代替 ADB 驅動真實 Core，涵蓋暫停識別、取消、時間戳順序、PNG 對應關係與重送：

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest \
  src/MaaMcpServer/tests/test_native.py src/MaaMcpServer/tests/test_battle_control.py -q
```

PowerShell 先設定 `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`，再執行同一條 pytest 命令。資源不在安裝目錄時，另設 `MAA_MCP_RESOURCE_DIR` 為其父目錄。未設定環境變數時會明確跳過此測試；離線通過不代表實機行為已確認。

Windows 原始碼 Debug 建置也須先執行 `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install`，執行期需求與上述 Debug 產物相同。

worker 使用獨立子程序與專用 stdin/stdout 管道，不繼承 MCP 的輸入輸出串流。stderr 記錄 PID 以及 `worker_started`、`core_load`、`resource_load`、`core_create`、`ready` 與請求階段；啟動或連線逾時會指出最後階段，不會誤稱戰鬥結果未知。逾時或取消仍會終止 worker 並釋放控制鎖；戰鬥命令中斷仍按 `outcome_unknown` 處理，不自動重試。

`battle_status` 與 `battle_connect` 分別回傳 `project_version`、`build`（包內 `BUILD-INFO.json`）與 `resources`（設定的資源根目錄及地圖索引 SHA-256）；缺少清單或索引時對應值為 null，不以 MCP SDK 版本代替專案版本。連線成功後另有 `core_version`。綁定前依唯一 stage/level ID 檢查索引與檔案，拒絕顯示編號（如 `TL-EX-1`）；缺圖會明確指出請求 ID 與資源根目錄，不自動下載或回退綁定。結果的 `map_source` 另含地圖檔案 SHA-256；這不代表畫面已驗證或跨版本相容。

選用實機冒煙測試須明確設定 `MAA_MCP_NATIVE_CORE`、`MAA_MCP_LIVE_ADDRESS` 與 `MAA_MCP_LIVE_STAGE`，再執行 `uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_stdio.py -k real_device`。資源、ADB 與連線設定可分別用 `MAA_MCP_RESOURCE_DIR`、`MAA_MCP_ADB_PATH`、`MAA_MCP_CONNECTION_CONFIG` 指定。先停止其他控制器，手動進入唯一 ID 對應的固定地圖戰鬥；測試只連線、綁定、截圖與關閉，不恢復、部署或退出戰鬥。條件不足會明確跳過；模擬 worker 的離線通過不等於 Windows 實機通過。

## 啟動與用戶端設定

先關閉其他控制同一裝置的 MAA/GUI 任務，手動進入固定地圖戰鬥。目前不支援自動選詞條、進入關卡、多階段鏡頭或重建接管前已部署的幹員。結算識別僅支援下文的有限標題比對。

同一個 `--user-dir` 有跨程序控制鎖。同一裝置請固定使用同一目錄；此鎖無法阻止使用其他目錄的服務或外部 GUI 操作裝置。

已適配召喚卡不再要求啟動時列舉 ID；未知互動仍拒絕，輸入狀態、在場證據與請求位置分開記錄。完整部署／撤退閉環尚未完成或實機驗收，詳見下文。

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

新會話接入手動暫停時，`battle_identify` 的嚴格布林參數 `request.adopt_manual_pause=true` 明確授權接管而不恢復，預設 false。Core 必須識別正向暫停證據，缺少運行按鈕不再足夠，入場標題過渡不能接管。識別只在暫停時查看卡牌；回傳 `pause_verified`、`safe_state`、同影格 `snapshot` / `image_path` 與 PNG，部分 OCR 亦保留證據。儲存失敗透過 `evidence_error` 明示，取消或 worker 故障可能無法提供最終證據，不會主動恢復戰鬥。

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

需要執行單一戰術動作時，優先使用 `battle_step`，讓預檢、必要的恢復、動作、動作後截圖與暫停確認都在同一個 Core 命令中：

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

`action` 可為 `deploy`、`retreat`、`skill` 或 `wait`；部署還須提供 `location` 和 `direction`。恢復手動暫停須明確 `adopt_manual_pause=true` 與新截圖的暫停證據。每次最多執行一個動作，先分析動作邊界，再請求暫停並回傳新截圖與 `pause_verified`。取消會通知 Core 並等待清理，worker 故障時暫停可能未知。`battle_step.timeout_ms` 預設 10000、上限 30000，不會自動延長呼叫者預算。

`captured_at_ms` 是取影格完成後、分析開始前記錄的主機 Unix 毫秒時間，不是裝置渲染時間。`capture_time_snapshot` 始終對應 `capture_image_path`（動作邊界），`return_time_snapshot` 對應 `return_image_path`（稍後的暫停驗證）；MCP 圖片內容仍按回傳影格、動作邊界影格排列。`observation_id` 按分析順序遞增而非取影格順序，因此動作邊界觀測的 ID 可能較大，但時間戳較早。系統時鐘調整會影響時間差，這些時間戳不構成運行時間硬限制。完全相同的操作重送保留原始時間戳與路徑，新截圖則記錄自己的取影格時間。

`battle_identify` 會回傳 `identified_count`、`unresolved_count`、`ambiguous_count` 和各卡牌的 `identity_reason`；空名稱快取不再抑制詳情 OCR。其他動作回傳 `outcome_unknown` 時，MCP `isError=true`；即使 `helper_returned` 或 `action_returned` 為 true，也不代表遊戲內成功。必須截圖確認，不可盲目重試。場上位置標為 `action_history_unverified`，敵人狀態為 null；首版不接受模型直接覆寫狀態。單獨的暫停請求不代表已確認暫停，恢復僅允許針對本服務請求過或明確接管的暫停。

一般命令的 `timeout_ms` 上限為 30000，下文的戰前批次檢查使用獨立上限。一般命令逾時或取消會終止 worker、使會話失效並保存未知結果；step 等待暫停清理，但無法保證裝置故障時暫停。`battle_close` 中斷一般命令或等待 step／戰前檢查清理，本身不暫停或退出遊戲。`battle_get_operation` 讀取持久結果；儲存失敗會明確報錯並阻止此服務繼續發命令。日誌及 `operations.sqlite3` 位於 `--user-dir`，一般截圖保留最近 128 張，關鍵證據另外保留，需自行清理。

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

## 有界等待與決策資料

等待不再需要用戶端分別呼叫恢復和暫停：

```json
{
  "request": {
    "session_id": "replace-with-session-id",
    "operation_id": "wait-001",
    "action": "wait",
    "duration_ms": 1000,
    "adopt_manual_pause": true,
    "timeout_ms": 10000
  }
}
```

`duration_ms` 為 100–8000，`timeout_ms` 至少比它大 1000；不得傳單位、位置或方向。時間採用主機單調時鐘，並非遊戲模擬時間；擷取與分析會增加實際耗時。Core 推進時持續採樣，保留末段最多 8 影格及邊界／回傳影格。取消或關閉服務會中止推進並盡力暫停；逾時、取消、證據寫入失敗或暫停未知不會回傳正常等待成功。外部暫停會回傳 `wait_interrupted`，不會擅自再次恢復；未確認終局的非戰鬥畫面即使隨後恢復為暫停戰鬥，仍回傳 `outcome_unknown` 和 `session_input_blocked=true`。裝置 I/O 卡死或程序崩潰仍可能無法暫停。

`terminal` 區分 `victory`、`defeat`、`unknown`、`still_in_battle`，附識別來源、原始比對信心值和 `terminal_evidence_path`。目前資源僅設定簡體中文與英文的明確成功／失敗標題，並非所有語言或畫面均經實機驗證。擊殺數達總數、黑畫面或離開戰鬥都不自動判勝；等待交易成功也不代表作戰勝利。看到非戰鬥畫面後，會話拒絕後續輸入，不在選單切換暫停。離場證據不參與一般 128 張輪換，需自行清理；使用者證詞不得代替截圖來源。

分行標題備援僅在曾觀察到可操作戰鬥畫面、目前仍辨識到戰鬥介面且沒有運行按鈕時，接受上方完整詞 `MISSION` 與下方完整詞 `ACCOMPLISHED`。兩詞須來自同一影格、各自得分至少 0.9，字框高度相近、左側對齊且上下相鄰；不跨截圖累積片段。`terminal.split_victory` 保留字框、得分及採納或拒絕原因，採納時來源為 `BattleControlVictorySplit`，信心值取較低分。完整成功標題與分行成功標題不互相衝突，但同時確認失敗時仍維持 unknown 與輸入隔離。單獨 `MISSION` 或通用的 `MISSION RESULTS`／行动结束不是勝利證據。合成回歸不代表真實動畫標題或獨立結算頁已通過辨識驗收。

完整靜態查詢先明確 `battle_attach(stage_name="act55side_ex01", mode="normal")`，再呼叫 `intel_query(kind="stage", identifier="act55side_ex01", complete_stage=true, session_id="…")`。它重用既有 provider，略過分頁快取，最多 100 次請求／120 秒；檢查區服、關卡、模式、跨頁來源及重複 ID。結構化契約要求 `game_id`、`server`、`mode`、`provenance`，`routes_page`／`spawns_page` 中的 `page`、`has_more`（可選 `total`），以及頂層 `routes`／`spawns` 陣列或分頁內 `items`。路線用 `route_id`；生成項用 `spawn_id`、`enemy_game_id`、`route_id`、`count`。未知 provider 結構不會猜測為已適配。

`coordinate_system="public_row_col"` 時依綁定地圖高度執行 `x=col, y=height-1-row`，點上新增 `maa_location`，二維格子陣列反轉列序；原始回應保留在 `pages`。`maa_grid` 不轉換，其他座標系保持未知。缺欄位、缺頁或 provider partial 回傳 `partial` 與逐節 `completeness`，不以空集合代替缺失；身分／版本衝突或 provider 失敗直接報錯。`expires_at` 為 Unix 秒，過期明確標記；省略代表新鮮度未知，5 分鐘快取不保證遊戲資料最新。仍須以實際 provider 驗收，模擬服務通過不證明五類資料齊全，靜態生成數也不含所有動態召喚物。

透過 `--loadout-config /absolute/loadout.json` 提供本場設定；啟動時讀取，修改後需重啟。範例不是現場識別結果：

```json
{
  "region": "cn",
  "stage_id": "act55side_ex01",
  "mode": "normal",
  "confirmed_by_user": false,
  "operators": [
    {"operator_game_id": "char_4228_closur", "actual_elite": 2, "equipped_skill_id": null}
  ]
}
```

區服、關卡和明確模式須一致。可選填 `actual_level`、`potential`、`module_id`、`module_level`、`skill_level`、`mastery`；省略為 unknown。快照的 `decision_context` 分別保留欄位來源、使用者確認和設定雜湊，不覆寫原生觀測，也不將使用者確認稱為視覺驗證。SP、技能階段、持續時間與天賦仍未知；不從有歧義的 game ID 候選中默選第一項。

`battle_evaluate_placement(session_id, placements)` 唯讀檢查候選全隊陣型。每項填 `operator_game_id`、`location`、`direction`，精英階段取設定，或明確填 `assumed_elite` 作假設。輸出基礎範圍、幾何友軍覆蓋、靜態部署類型相容性及占格衝突；未知階段／範圍不套職業預設模板。不驗證現場占位、實際治療資格、生存或全域最佳化，也不含技能、模組、天賦和地形特殊效果。現場敵人追蹤仍不支援。

## 場景證據、準備觀察與受限操作

`scene` 區分 `running / paused / transition / terminal / unknown`。每張採樣影格均檢查終局，正向終局證據優先於殘留戰鬥 UI。確認戰鬥後，仍在戰鬥內的 `transition` 以 `input_block_reason=pause_verification_pending` 禁止輸入，不再直接視為永久終局隔離。恢復必須連續擷取兩張帶正向暫停證據的新影格；執行或過渡影格會重設計數。不能僅憑執行按鈕消失認定暫停。暫停收尾的唯讀驗證預算取 3000 ms 與 ｢已觀測到的最大單張影格擷取及分析耗時的兩倍 + 1000 ms｣ 中的較大值，為兩張影格確認及輪詢留出時間，但始終受命令剩餘期限限制，取消會停止驗證；持續無法確認時仍禁止輸入。

`battle_observe`、`battle_capture_evidence` 和 `battle_collect_terminal(request)` 可在不輸入的情況下收集恢復證據。`battle_collect_terminal.duration_ms` 為 100–8000，不能超過 `timeout_ms`；`status=succeeded` 僅表示擷取完成，不表示暫停恢復或戰鬥勝利，須檢查 `pause_verified`、`session_input_blocked` 和 `terminal`。鎖定期間接收的修改命令即使恰好在觀察時完成恢復，也不會執行；須檢查新證據後發起新操作，必要時明確授權接管手動暫停。不會重播舊動作或改寫歷史結果。戰鬥後的未知／非戰鬥畫面、終局／衝突證據、調速回讀失敗仍以 `input_block_reason=terminal_or_safety_quarantine` 永久隔離，正向暫停證據不能解鎖。`session_terminal` 保留結果與證據路徑，黑畫面不抹除已確認結果，衝突勝敗保持 unknown。Core 另有 `set_speed`、`finalize`、`prepare`、`bind` 命令；準備任務以 `preparation_only=true` 初始化，綁定沿用同一控制器。

暫停識別先完整匹配 `PAUSE`；未達到 0.9 時，在 `[560,350,160,70]` 區域檢查國服的 `暂停中`。中文既支援單框整詞，也支援同一次 OCR 中按空間順序排列的兩至三個同行片段，每片段均須至少 0.9；僅允許有限的鄰接間隙和框重疊，拒絕缺字、額外前後綴、異行、異常重疊及區域外文字，不跨影格拼接。整詞來源為 `BattleControlPausedLocalized`，分框來源為 `BattleControlPausedLocalizedSplit`。`pause_evidence` 保留 `fragments`、完整文字、片段最低分 `score`、合併框、影格 ID／時間及 `accepted`／`rejection_reason`。`PAUSEX` 不是替代證據；執行按鈕和終局判定仍優先，解除隔離仍須連續兩影格，不自動接管暫停。

暫停識別失敗時，`pause_diagnostics` 保留同影格 ID／時間、設定 ROI、要求分數、前處理設定和可空匹配結果。`localized.candidates` 與正式中文規則共用一次取消目標文字篩選的 OCR 採集，`candidate_source=same_frame_localized_pause_rule_ocr`；`localized.fragment_rule` 記錄規則是否接受及拒絕原因，只有規則接受後才形成暫停證據，不能把單個候選直接升級為證據。英文診斷候選仍來自獨立的無目標文字篩選採集，不替代正式匹配。匹配為 null 不等於測得低分。原生幾何迴歸涵蓋報告中的重疊框，合成文字迴歸走正式 OCR 鏈路，但均不證明遊戲字型、特效遮擋或外服適配；部署後的真實 OCR、1x／2x 和完整戰鬥驗收仍須本機裝置複驗，速度閾值不變。

`skill_ready` 是 true 或 null，分類器否定不再冒充已確認冷卻。`skill_evidence` 保留同影格 ID、時間、ROI、基點、類別分數和機率。`probabilities` 順序為 `c, n, y`，`score` 是勝出類別的分數，不一定是就緒類別 `y` 的分數；僅 `y` 勝出且分數至少 0.9 才報告 true。技能階段／彈藥／冷卻仍可未知，分數並非校準後正確率。請保留 `asst.log` 的 `Battle frame` / `Battle command result`、`battle-control/<session>/` 的 PNG 與 `operations.sqlite3`。合成回歸不證明真實 OCR、ROI 或按鈕適配；仍須本機複驗，不要求本機圖片作為雲端開發輸入。

連線後、attach 前呼叫 `battle_observe_preparation(stage_name, mode)`，唯讀擷取手動開啟的編隊頁。回傳名稱、候選 ID、名稱框與證據；所選技能、練度、助戰身分未知，不開啟詳情。以 `battle_attach(..., preparation_id=..., confirm_preparation_unchanged=true)` 關聯最新快照，只代表呼叫者重新確認，不是連續視覺驗證。修改編隊／技能／關卡／帳號後呼叫 `battle_invalidate_preparation` 並重擷取。新擷取、綁定消費或關閉後舊快照不能重用，現場身分不符在 `decision_context.preparation` 標為衝突。

原生批次檢查使用獨立的**修改型**工具 `battle_inspect_preparation`，宿主只呼叫一次：

```json
{
  "request": {
    "operation_id": "preparation-001",
    "stage_name": "main_01-07",
    "mode": "normal",
    "timeout_ms": 120000
  }
}
```

Core 依等級標籤定位有人的欄位，保留名稱無法讀取的項目，讀取等級 OCR 與正向精一／精二模板，再逐一開啟身分唯一的普通成員頭像。確認詳情身分後只開啟技能頁籤，有界捲動技能清單。完整有序清單中唯一的選取標記才產生 `selected_skill=1|2|3`；`equipped_skill_id` 還要求身分唯一且 `battle_data.json` 含有序 `skillIds`，靜態 ID 不單獨證明實際裝備。不點選選人卡片、技能選項、編隊確認或開始按鈕。目前跳過助戰詳情；缺少精英模板保持 null，**不推定為精零**，等級失敗不補 1。空位不是成員；等級標籤缺失不能證明編隊完整。

檢查 `snapshot.scan_status=complete|partial`、各成員 `fields`（值、來源、驗證、原因、ROI、影格證據）、`evidence_frames`、`formation_restored`、`roster_unchanged` 與 `preparation_valid`。complete 只涵蓋已定位成員，partial 不代表完整配置。PNG 路徑與擷取時間關聯原始影格，所選技能證據另帶跨影格捲動順序。預設期限 120000 ms，**最大 180000 ms**，不同於一般命令的 30000 ms。取消／關閉會通知 Core 並等待有界收尾；只允許對身分相符且已識別的詳情頁在原期限內返回一次，未知頁面不盲點關閉。工作程序失敗時恢復狀態為未知。同一操作 ID 只接受相同參數；重播回傳原證據，不導覽，也不重新啟用已失效快照。attach 前可用 `battle_status.preparation_session_id` 查詢 `battle_get_operation` 的持久結果。

僅有效的最新結果可沿用明確重新確認的 attach 關聯。新擷取使舊關聯失效；頁面／編隊變化、證據失敗或掃描中斷不建立有效關聯。`decision_context.preparation.observed_fields` 獨立保留原生觀測並記錄與外部配置的衝突，不覆寫任一來源。這是戰前測量，不是帳號／關卡／配置的連續驗證，也不是戰鬥現場測量。識別複用既有資源並有合成 Debug Core 迴歸，**不等於實機驗收**；真實版面與選取標記相容性仍須本機驗證。

### 離線單位資料與自有實例

`battle_get_unit_info(game_id)` 不需連接裝置或設定情報 provider，讀取隨包的 `resource/unit_info.json`，回傳 `unit`、`source`、`resource_sha256`、`scope`：TOKEN／TRAP／幹員分類、效果、基礎屬性關鍵影格及範圍 ID、技能引用與定義、天賦 blackboard、帶條件的 `tokenKey`／`overrideTokenKey` ID 關聯。靜態值不是現場卡片 `cost`／`available`／`cooling` 或實際配置測量；使用者設定不參與視覺 ID 消歧。

`base_range_definitions` 保留遊戲相對行列座標，座標約定見 `base_range_coordinate_system`；不是實測 MAA 格子。

能力含 `value=true|false|null`、`source`、`reason`、`game_data_value`、`live_verified`。已審閱適配值表示受限互動約定，不證明引擎策略或實機通過；`execution_support` 另行標示僅輸入嘗試或不支援的操作。unknown 不是許可。首個召喚適配是醫療探機 `token_10000_silent_healrb`，普通拖曳且 `direction="None"`；其他 TOKEN／TRAP 互動未知並拒絕。同格共存與召喚物技能不支援。

使用 `battle_step(action="deploy", unit_id=..., card_id=...)`。`game_id` 是種類，`card_id` 是具體觀測卡，`instance:<operation_id>` 是自有部署嘗試。先綁定卡片再檢查姓名，同名多卡可依卡片部署，遊戲 ID 仍須唯一；恢復後重新核驗身分、布局、外觀、可用性和 30 秒新鮮度。不傳 `--allow-summon` 允許已適配卡；舊參數保留為明確限制並發出遷移警告，不授權未知互動。原生 `allowed_summons=[]` 排除所有召喚，省略才啟用適配卡。

C++ 型別分開記錄 presence、位置證據與操作狀態；輸入不證明落地，費用／冷卻變化不提交占格。修改型有界詳情檢查記錄兩影格姓名／圖示證據，不證明本次實例在場或選中格；未知實例保留請求格占用。`battle_step(action="retreat", unit_id="instance:...")` 先查能力、拒絕同種或鄰格歧義，再查身分與正向撤退按鈕。**尚無真實選中格錨點或明確離場規則，無錨點不點撤退按鈕，未知占格不釋放。** 缺按鈕回傳 `selection_unverified`，不推定永久不可撤退；明確 false 才回傳 `not_retreatable`。被動觀察不點擊／恢復，不因計時或單影格漏識別判消失；已驗證終局可標為離場。重送保留日誌與截圖，不再輸入；不支援重啟／中途接入重建。

正向詳情影格只證明所選種類存在（`selected_kind_present`），不證明本次部署成功；無格子錨點時自有實例 `presence` 仍為 unknown。撤退圖示匹配只記為 `retreat_button_detected`，不證明啟用；缺少可用性規則時 `retreat_button_available` 保持 null。

舊 `battle_deploy` 接受可選 `card_id`；舊部署／撤退／技能入口起始仍須戰鬥運行，使用相同有界預檢與暫停收尾，不可繞過能力／實例／位置檢查。沒有選中格錨點時，普通幹員技能／撤退也拒絕，不依未驗證歷史操作。

TL／TL-EX 使用 `act55side_*`（如 `act55side_ex01`，非 `TL-EX-1`）。｢自爆榴弹｣（爆炸型關卡裝置） `trap_341_dsjtzd` 可離線查詢：基礎費用 20、再部署 20 秒、範圍 `x-4`、未列技能、天賦 blackboard 暈眩 5 秒。**再部署時間不是爆炸倒數。** 原表不證明拖曳／撤退策略；缺少爆炸、明確消失和翻板變化的可靠實圖規則，動態覆蓋 unknown／unsupported，TL 部署回傳 `dynamic_terrain_unknown`。預估計時不修改靜態地圖；不宣稱 TL 閉環完成或實機通過。

建置 Debug `res_updater` 後，以本地固定版本的角色／技能表重新生成：

```bash
python tools/ResourceUpdater/generate_unit_info.py \
  --updater build-mcp/bin/Debug/res_updater \
  --input path/to/excel --output resource/unit_info.json
```

`tools/ResourceUpdater/unit_info_source.json` 固定儲存庫、commit、區域及角色、升變角色、技能、範圍四份輸入的 SHA-256；包裝腳本核驗後呼叫唯一 C++ 生成器。執行時不下載或依賴 Wiki。生成 JSON 每筆一行、排除 Prettier；一般資源更新僅在輸入目錄提供 `unit_info_source.json` 時重生成伴隨檔。升級聲明來源須重新生成並驗證；來源雜湊不保證安裝遊戲相容。外部 raw 關卡／敵人快照仍須另行設定，不隨包提供。

`battle_set_speed(request)` 接受 `session_id`、`operation_id`、`multiplier=1|2` 與 `timeout_ms`。只在明確暫停時讀取速度、必要時切換一次並回讀，回傳前後證據，不變更暫停所有權、不自動恢復；重複設 2 倍不切回 1 倍。速度未知不點擊，切換後無法確認則鎖定輸入。是否加速完全由宿主決定。等待仍是主機時間：依已觀測取影格成本預留暫停預算，不足時拒絕恢復；`cleanup_reserve_ms`、`resumed_interval_upper_bound_ms`、`deadline_margin_ms` 不保證精確遊戲時間。

1X 優先採用至少 0.9 的 OCR；不足時，`BattleControlSpeedOneTemplate` 在同一速度區域重用既有 `BattleSpeedUp.png` 模板，同樣要求至少 0.9。`one_x_ocr_evidence` 與 `one_x_template_evidence` 分別保留原始結果，`one_x_evidence` 是選用結果。2X 仍使用 `BattleSpeedUpCheck`，1X／2X 證據衝突保持 unknown，不能憑未識別到 2X 反推 1X。模板迴歸不等於已驗證報告中的遊戲截圖。

## 固定原始關卡快照

`--intel-config` 支援 `provider="raw"`，只讀可信任本機檔案，不自動下載。設定 `root`（絕對根目錄）、`repository`、`commit`（40 位提交）、`data_version`、`region`、`stages`、`enemy_database`。`stages` 將 stage ID 映射至 `{"path":"相對路徑","sha256":"64位雜湊"}`，`enemy_database` 使用相同檔案結構。每檔上限 64 MiB、核驗 SHA-256，拒絕越出根目錄。

透過已綁定會話呼叫 `intel_query(..., complete_stage=true)`。分別轉換自頂向下地圖與自底向上路線，輸出 `maa_grid`，等待檢查點不當移動點。一條 SPAWN action 對應一筆生成記錄，保留 count、delay、interval、隱藏組和阻塞欄位。敵人等級繼承、定義旗標與關卡覆寫會解析，支援 Key/Value 與 key/value；未知機制與非 SPAWN actions 保留，不偽造空集合。`mode_modifiers` 只選難度 mask，不宣稱解釋符文；`alignment=unknown`。`mode=normal|challenge` 表示難度，非演習／實戰。外部 provider 僅在 schema 宣告支援時接收 mode，快取依 mode 隔離。雜湊與版本聲明不證明本場遊戲相容或獨立 provider 互通；外部 provider 資料包不隨服務重新散布。

上述不重新散布只指外部 raw 關卡／敵人輸入；前述離線單位資料另行隨包提供。

## Core 協議

初始化 `AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})` 後呼叫 `AsstStart`，等待 `SubTaskExtraInfo` 的 `what="BattleControlReady"`（協議版本 1）。之後透過 `AsstSetTaskParams` 傳入 `session_id`、`command_id`、`action`、`args`、`timeout_ms`，等待 `what="BattleControlResult"`。每次只允許一個命令，收到回呼後才可繼續投遞。

動作名稱為 `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused`、`step`。`step` 每次只接受一個已預檢的戰術動作，並在動作後立即取影格、請求暫停、驗證並回傳暫停影格；它無法保證裝置底層 I/O 或整個 MCP 程序故障時一定暫停。更新參數只加入佇列，裝置操作均在 Core 任務執行緒執行。原生呼叫者仍須自行實作外部 watchdog 與去重；單獨使用 C ABI 無法保證底層 I/O 在期限內返回，MCP 服務提供程序層級 watchdog。自動試驗與最佳化工具留待後續實作。

`identify` 接受可選的嚴格布林參數 `args.adopt_manual_pause`，預設 false。明確接管只取得已識別暫停的控制權，不切換暫停，也不授權此次識別恢復戰鬥。

`step` 也接受 `args.action="wait"` 與 `args.duration_ms`，不傳單位參數。執行時可經 `AsstSetTaskParams` 傳送 `{"session_id":"…","cancel_command_id":"…"}`；它只設定對應命令的取消訊號，不操作裝置或產生第二份結果。遲到的取消不影響下一條命令。原命令仍以原回呼回傳清理結果，MCP 按原 `operation_id` 保存與去重。
