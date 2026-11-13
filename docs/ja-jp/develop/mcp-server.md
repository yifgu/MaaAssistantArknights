---
icon: mdi:robot
order: 20
---

# 戦闘 MCP Server

`src/MaaMcpServer` は、MaaCore を独立した worker プロセスで実行するローカル stdio MCP サーバーです。Python 3.11+、uv、このブランチからビルドした Core が必要です。旧版には `BattleControl` タスクがありません。実験的な戦闘操作機能であり、危機契約の自動最適化ツールではありません。

## ビルドとインストール

リポジトリのルートで実行します。[Linux ビルドガイド](./linux-tutorial.md) または対応するプラットフォームのガイドに従い、コンパイラーと CMake を準備してください。Windows では Visual Studio の開発者ターミナルを使用します。

```bash
git submodule update --init --recursive src/MaaUtils
python tools/maadeps-download.py
cmake --preset linux-x64 -B build-mcp -DBUILD_WPF_GUI=OFF -DINSTALL_RESOURCE=ON
cmake --build build-mcp --config Debug --target MaaCore -j 2
cmake --install build-mcp --config Debug --prefix "$PWD/build-mcp/install"
uv sync --project src/MaaMcpServer
```

Windows x64 では `linux-x64` を `windows-x64` に、macOS では `macos-arm64` または `macos-x64` に変更します。異なるプラットフォームでビルドディレクトリを共有しないでください。インストール先には Core、実行時依存ライブラリ、`resource/` が必要です。`--resource-dir` で `resource/` の親ディレクトリを別途指定できます。

## GitHub Actions の成果物を使う

自分のリポジトリで **Actions → Release Pipeline → Run workflow** を開き、MCP ブランチを選択し、`mcp_only` を有効にして `mcp_configuration` を選びます。

- `RelWithDebInfo`：既定の完全な実行パッケージ。Python、uv、ADB、Visual Studio の別途インストールは不要です。
- `Debug`：開発・テスト用。Linux はそのまま実行でき、Windows は再配布できない Debug CRT をローカルで用意する必要があります。

どちらも先に Debug Core をビルドして全テストを実行し、その後に選択した構成をパッケージ化します。GUI のビルドや Release 公開は行いません。

`mcp_platform` の既定値は `all` です。`win-x64` または `linux-x64` を選ぶと必要なプラットフォームだけをビルドし、runner の重複使用を避けられます。

```bash
gh workflow run ci.yml --ref <branch> -f mcp_only=true -f mcp_configuration=RelWithDebInfo
```

関連 PR でも同じ `Battle MCP` ジョブが実行されます。`.github/workflows/mcp-build.yml`（**Battle MCP Bundle**）は通常と Nightly で共用します。既定ブランチへの反映前は既存の Release Pipeline から手動実行できます。手動 MCP ビルドは独立した並行実行グループを使うため、同じブランチの PR ビルドにキャンセルされません。

成功した実行の **Artifacts** から `MAAMcp-win-x64-RelWithDebInfo` または `MAAMcp-linux-x64-RelWithDebInfo`（Debug 選択時は `-Debug`）を取得し、同名の `.zip` または `.tar.gz` を展開します。`core/`（Core、ネイティブ依存、リソース、ADB ControlUnit）、`python/`（独立した Python とロック済みの全実行・テスト依存）、`adb/`、起動スクリプト、ソースとテスト、wheel/sdist、依存ライセンス、コミットと実行 URL を記録した `BUILD-INFO.json`、SHA-256 が含まれます。Windows 実行パッケージには再配布可能な VC++ CRT も同梱されます。チェックアウト、再ビルド、オンラインでの Python パッケージインストールは不要です。Linux は互換性のある x64 OS とシステム glibc が必要であり、コンテナーや OS 自体は含みません。

Windows x64 ではダウンロード先で実行します：

```powershell
Expand-Archive .\MAAMcp-win-x64-RelWithDebInfo.zip .\mcp-test
Set-Location .\mcp-test
.\run-mcp.cmd --check
.\run-mcp.cmd --address 127.0.0.1:5555
```

Linux x64 ではダウンロード先で実行します：

```bash
mkdir mcp-test
tar -xzf MAAMcp-linux-x64-RelWithDebInfo.tar.gz -C mcp-test
cd mcp-test
./run-mcp.sh --check
./run-mcp.sh --address 127.0.0.1:5555
```

`--check` は同梱 ADB、Python 依存、Core とリソースを読み込み、端末には接続しません。通常起動は MCP 入力を待つ stdio サービスで、Web ページはありません。クライアントの `command` は同梱の `python/python.exe`（Windows）または `python/bin/python3`（Linux）、`args` は `["-I","-B","/absolute/mcp-test/run_mcp.py","--address","127.0.0.1:5555"]` とし、すべて絶対パスを使います。状態の保存先はパッケージ内の `state/` で、`--user-dir` で別の書き込み可能な場所を指定できます。

全オフラインテストには Debug パッケージの `run-mcp.cmd --self-test` または `./run-mcp.sh --self-test` を使います。Windows は Visual Studio 2026/Build Tools の C++ デスクトップワークロードと Windows SDK を用意し、先に `powershell -NoProfile -ExecutionPolicy Bypass -File tools/PrepareMcpRuntime.ps1 -CoreDir core` を実行します。**Debug CRT は再配布できず、通常の VC++ Redistributable では代用できません。** スクリプトはローカル開発 DLL のみをコピーします。変更後のディレクトリを再配布しないでください。CI は別の Debug ディレクトリでテストし、成果物に Debug CRT を含めません。`RelWithDebInfo` は無効化されたアサーションによる誤解を避けるため `--self-test` を拒否します。

実機では下記の接続・マップ確認が必要です。古い Core、リソース、別コミットのテストを混在させないでください。外部情報プログラムとデータは任意のプラグインで、別途設定します。ゲームやエミュレーター、クラウドモデルの自動呼び出しは含まれません。

Windows Debug と MaaFramework の Release ControlUnit は ABI が互換ではないため、Debug パッケージにはこの任意 DLL を含めず、`--touch-mode MaaFwAdb` を拒否します。既定の `maatouch`、`adb`、`minitouch`、または完全な `RelWithDebInfo` パッケージを使用してください。

## ローカルテスト

オフラインの単体テスト、実際の MCP stdio ハンドシェイク、模擬情報サーバーのテストに端末は不要です。

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

実際の Debug Core によるタスク登録、引数の拒否、コールバック、マップ投影の確認も端末なしで実行できます。合成画像テストは ADB の代わりにローカルの模擬通信で実際の Core を動かし、停止中の認識、キャンセル、時刻順序、PNG の対応関係、再送を確認します。

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest \
  src/MaaMcpServer/tests/test_native.py src/MaaMcpServer/tests/test_battle_control.py -q
```

PowerShell では先に `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"` を設定し、同じ pytest コマンドを実行します。リソースが別の場所にある場合は、その親を `MAA_MCP_RESOURCE_DIR` に設定します。環境変数がなければ明示的にスキップされます。オフラインテストの成功は実機動作の保証ではありません。

Windows のソースからの Debug ビルドでも、先に `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install` を実行します。前提条件は Debug パッケージと同じです。

worker は MCP の入出力を継承せず、専用 stdin/stdout パイプを持つ別プロセスで動作します。stderr に PID と `worker_started`、`core_load`、`resource_load`、`core_create`、`ready`、各リクエストの段階を記録します。起動・接続のタイムアウトは最後の段階を示し、戦闘結果不明とは表示しません。タイムアウトやキャンセル時は worker を終了し制御ロックを解放します。戦闘コマンドの中断は引き続き `outcome_unknown` とし、自動再試行しません。

`battle_status` と `battle_connect` は `project_version`、`build`（隣接する `BUILD-INFO.json`）、`resources`（設定されたリソースルートとマップ索引の SHA-256）を別々に返します。マニフェストや索引がなければ該当値は null です。MCP SDK のバージョンをプロジェクトのバージョンとして使いません。接続成功後は `core_version` も返します。紐付け前に一意の stage/level ID で索引とファイルを確認し、`TL-EX-1` などの表示コードを拒否します。不足時は要求 ID とリソースルートを示し、自動ダウンロードや代替マップへの紐付けはしません。結果の `map_source` はマップファイルの SHA-256 も含みますが、画面の検証済み判定や異なるバージョン間の互換性を保証しません。

任意の実機スモークテストは `MAA_MCP_NATIVE_CORE`、`MAA_MCP_LIVE_ADDRESS`、`MAA_MCP_LIVE_STAGE` を明示してから `uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_stdio.py -k real_device` を実行します。リソース、ADB、接続設定は `MAA_MCP_RESOURCE_DIR`、`MAA_MCP_ADB_PATH`、`MAA_MCP_CONNECTION_CONFIG` で指定できます。他のコントローラーを停止し、一意 ID に一致する固定マップの戦闘を手動で開始してください。テストは接続・紐付け・撮影・切断のみで、再開・配置・戦闘放棄はしません。条件不足は明示的にスキップされます。模擬 worker のオフライン成功は Windows 実機動作の保証ではありません。

## 起動とクライアント設定

同じ端末を操作する別の MAA/GUI タスクを停止し、固定マップの戦闘を手動で開始してください。契約選択、ステージ開始、多段階マップのカメラ移動、接続前に配置済みのオペレーターの復元には対応していません。終了判定は後述の限定的なタイトル照合のみです。

同じ `--user-dir` はプロセス間ロックで保護されます。同じ端末には常に同じディレクトリを使用してください。別ディレクトリのサーバーや外部 GUI による操作は防げません。

対応済み召喚カードは起動時の ID 列挙が不要です。未知の操作は拒否し、入力状態・存在証拠・要求位置を分離します。配置／撤退の完全なループは未完成・実機未検証です。詳細は後述します。

```bash
uv run --project src/MaaMcpServer maa-battle-mcp \
  --core-dir "$PWD/build-mcp/install" \
  --adb-path /absolute/path/to/adb \
  --address 127.0.0.1:5555 \
  --user-dir "$PWD/build-mcp/session"
```

プロセスは MCP クライアントの入力を待機します。Web ページの公開や自動接続はしません。以下のパスを絶対パスに置き換えてください。Windows の JSON パスには `/` またはエスケープしたバックスラッシュを使用します。

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

`battle_connect`、`battle_attach(stage_name="main_01-07")`、`battle_capture_evidence`、`battle_observe` の順で呼び出します。ステージ ID は画面と一致している必要があります。マップの指定は呼び出し側の責任で、自動認識ではありません。`battle_get_map` は静的なマスと 2 種類の画面投影を返します。不明な名前は null です。`battle_identify` は詳細画面を開いて認識し、一時停止を要求します。実行後に画像を確認してから `battle_set_paused(paused=false)` で明示的に再開してください。

手動一時停止の引き継ぎには、`battle_identify` の厳密な boolean 引数 `request.adopt_manual_pause=true` が必要です。既定値は false で、再開を許可しません。Core は明確な停止証拠を要求します。実行ボタンの不在だけでは不十分で、入場タイトルの遷移は引き継げません。停止中にカードを確認し、部分 OCR でも `pause_verified`、`safe_state`、同一フレームの `snapshot` / `image_path` と PNG を返します。保存失敗は `evidence_error` に記録され、キャンセルや worker 障害では最終証拠が得られない場合がありますが、自動再開しません。

配置リクエストの例です。セッション ID と観測されたユニット ID を置き換えます。ツール引数には `request` オブジェクトが必要です。

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

`battle_retreat` と `battle_use_skill` は `location` と `direction` を省いた同じ形式です。新しい操作には新しい `operation_id` を使います。同一リクエストの再送は保存済み結果を返し、同じ ID で引数を変えると拒否されます。コスト不足や再配置待ちは即座に失敗し、無期限には待機しません。画像ツールは同じフレームの PNG と観測をクライアントのマルチモーダルモデルに返します。サーバー自身がクラウドモデルを呼び出すことはありません。

単一の戦術操作には、事前検証、必要な再開、操作、操作直後の撮影、一時停止確認を 1 つの Core コマンドで行う `battle_step` を優先してください。

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

`action` は `deploy`、`retreat`、`skill`、`wait` です。配置には `location` と `direction` も必要です。手動停止の再開には `adopt_manual_pause=true` と新しい停止証拠が必要です。操作は最大 1 回で、操作境界を解析してから停止を要求し、新しい画像と `pause_verified` を返します。キャンセル時は Core の後処理を待ちますが、worker 障害時は停止が不明になる場合があります。`battle_step.timeout_ms` は既定 10000、上限 30000 で、呼び出し側の予算を勝手に延長しません。

`captured_at_ms` は画像取得直後、解析前に記録したホストの Unix ミリ秒時刻で、端末の描画時刻ではありません。`capture_time_snapshot` は `capture_image_path`（操作境界）、`return_time_snapshot` は `return_image_path`（その後の停止確認）に対応します。MCP 画像は従来どおり返却時の画像、操作境界の画像の順です。`observation_id` は取得順ではなく解析順に増加するため、操作境界の ID が大きくても時刻は早い場合があります。システム時計の調整は時間差に影響し、これらの時刻は実行時間の厳密な上限ではありません。同一操作の再送は元の時刻とパスを保持し、新しい撮影にはその取得時刻を記録します。

`battle_identify` は `identified_count`、`unresolved_count`、`ambiguous_count` とカードごとの `identity_reason` を返します。空の名前を持つキャッシュ項目が詳細 OCR を抑制することはなくなりました。その他の操作が `outcome_unknown` を返すと MCP の `isError=true` になります。`helper_returned=true` や `action_returned=true` でもゲーム内の成功は保証されません。再撮影して確認し、無条件に再試行しないでください。配置位置の情報源は `action_history_unverified`、敵の状態は null です。モデルによる状態の直接上書きには対応しません。単独の一時停止要求は停止確認と同じではなく、再開は本サーバーが要求または明示的に引き継いだ停止に限定されます。

通常コマンドの `timeout_ms` 上限は 30000 で、後述の戦闘前一括確認は別の上限を使います。通常の期限切れ／キャンセルでは worker を終了してセッションを無効化し、不明な結果を保存します。step は停止後処理を待ちますが、端末故障時の停止は保証しません。`battle_close` は通常コマンドを中断、または step／戦闘前確認の後処理を待ち、それ自体では停止・終了しません。`battle_get_operation` は永続結果を読み、保存失敗時は明示的に報告して以後のコマンドを拒否します。`--user-dir` にログと `operations.sqlite3` を保存し、通常画像は直近 128 枚、重要証拠は別途保持します。手動整理が必要です。

## 任意の情報サーバー

情報サーバーとデータスナップショットを別途準備し、`--intel-config /absolute/intel.json` を指定します。これは信頼されたローカル起動設定であり、モデルに任意のプログラムを指定させる仕組みではありません。

```json
{
  "provider": "intelligence",
  "command": "uv",
  "args": ["run", "--project", "/absolute/arknights-mcp", "arknights-mcp", "serve", "--transport", "stdio"],
  "region": "cn"
}
```

PRTS には `{"provider":"prts","command":"prts-mcp","region":"cn"}` を使用できます。アダプターが stdio と構造化出力を要求します。`intel_query` は `stage/operator/enemy` に対応し、Intelligence ではゲームの一意 ID、PRTS ではステージ ID またはオペレーター／敵の中国語名を指定します。Intelligence の `routes_page` と `spawns_page` は 1 始まりで、各ページは 50 件です。情報源、制限、ページ情報を保持し、5 分間キャッシュします。PRTS のステージ問い合わせは敵情報のみで、マス、経路、ウェーブを提供せず、別リージョンのデータへ暗黙に切り替えません。静的情報は現在の戦況ではなく、外部マップの自動統合や補正もしません。

## 時間制限付き待機と判断資料

クライアントで再開と停止を別々に呼び出さずに待機できます。

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

`duration_ms` は 100–8000 で、`timeout_ms` に少なくとも 1000 ms の余裕が必要です。ユニット、位置、方向は指定できません。時間はホストの単調時計で計測し、ゲーム内時間ではありません。取得・解析の負荷により実際の所要時間は増えます。Core は進行中に撮影し、末尾最大 8 フレームと境界／返却フレームを保持します。キャンセルや切断時は進行を中断して停止を試みます。期限切れ、キャンセル、保存失敗、停止未確認は正常な待機成功になりません。外部からの一時停止には `wait_interrupted` を返し、勝手に再開しません。終局を確認できない非戦闘フレームがあれば、その後に一時停止中の戦闘を認識しても `outcome_unknown` と `session_input_blocked=true` を返します。端末 I/O の停止やプロセス障害では一時停止できない場合があります。

`terminal` は `victory`、`defeat`、`unknown`、`still_in_battle` を区別し、認識元、生の照合信頼度、`terminal_evidence_path` を返します。現在のリソースは中国語簡体字／英語の明確な成功・失敗タイトルのみで、全言語・画面の実機対応を保証しません。撃破数の達成、黒画面、戦闘からの離脱は勝利と見なしません。待機処理の成功と戦闘勝利も別です。非戦闘画面を確認したセッションは以後の入力を拒否し、メニューで停止を切り替えません。離脱時の証拠は通常の 128 枚の循環削除から除外され、手動整理が必要です。ユーザーの証言は画像証拠と分けてください。

分割タイトルの補完は、操作可能な戦闘画面を観測済みで、現在も戦闘 UI が認識され、実行中ボタンが見えない場合に限り、上段の完全一致語 `MISSION` と下段の `ACCOMPLISHED` を受理します。両語は同一フレーム内で各スコアが 0.9 以上、文字枠の高さが近く、左揃えで上下に隣接する必要があります。別フレームの断片は結合しません。`terminal.split_victory` は文字枠、スコア、受理または拒否理由を保持し、受理時の認識元は `BattleControlVictorySplit`、信頼度は低い方のスコアです。完全な成功タイトルと分割成功タイトルは矛盾扱いしませんが、敗北も確認した場合は unknown と入力隔離を維持します。単独の `MISSION` や一般的な `MISSION RESULTS`／行动结束は勝利の証拠ではありません。合成回帰は実際のアニメーションタイトルや独立した結果画面の認識を保証しません。

完全な静的問い合わせは `battle_attach(stage_name="act55side_ex01", mode="normal")` で明示的に紐付けてから、`intel_query(kind="stage", identifier="act55side_ex01", complete_stage=true, session_id="…")` を呼び出します。既存 provider を再利用し、ページキャッシュを使わず、最大 100 リクエスト／120 秒で収集します。リージョン、ステージ、モード、ページ間の出典と重複 ID を確認します。構造化契約は `game_id`、`server`、`mode`、`provenance`、`routes_page`／`spawns_page` の `page` と `has_more`（任意の `total`）、最上位の `routes`／`spawns` 配列またはページ内の `items` を要求します。経路は `route_id`、生成項目は `spawn_id`、`enemy_game_id`、`route_id`、`count` を使います。不明な provider 形式は推測しません。

`coordinate_system="public_row_col"` は紐付けた高さで `x=col, y=height-1-row` に変換し、各点に `maa_location` を追加、二次元タイル配列の行順を反転します。元の応答は `pages` に残します。`maa_grid` は変換せず、他の座標系は不明のままです。項目・ページ不足や provider の partial は `partial` と節ごとの `completeness` を返し、欠落を空集合にしません。識別／バージョン不一致や provider 障害はエラーです。`expires_at` は Unix 秒で、有効期限切れを明示します。省略時は鮮度不明で、5 分キャッシュは最新ゲームデータの保証ではありません。実 provider の受け入れ確認が必要で、模擬サービスの成功は 5 分類の情報完備や動的召喚数を証明しません。

`--loadout-config /absolute/loadout.json` でこの戦闘の構成を渡します。起動時に読み込むため変更後は再起動が必要です。以下は観測結果ではなく設定例です。

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

リージョン、ステージ、明示モードは一致する必要があります。`actual_level`、`potential`、`module_id`、`module_level`、`skill_level`、`mastery` も指定でき、省略は unknown です。スナップショットの `decision_context` は項目ごとの情報源、ユーザー確認、設定ハッシュを別途保持し、ネイティブ観測を上書きせず、ユーザー確認を視覚的確認と呼びません。SP、スキル段階、持続時間、素質は不明のままです。曖昧な game ID 候補の先頭を勝手に選びません。

`battle_evaluate_placement(session_id, placements)` は候補編成全体を読み取り専用で確認します。各項目に `operator_game_id`、`location`、`direction` を指定し、昇進段階は設定から取得するか `assumed_elite` で仮定を明示します。基本範囲、幾何学的な味方カバー、静的配置適合性、マス競合を返します。不明な段階／範囲に職業の既定テンプレートは使いません。現場の占有、回復対象条件、生存や最適性は検証せず、スキル・モジュール・素質・特殊地形効果も含みません。敵のリアルタイム追跡は未対応です。

## 場面の証拠、準備観測と制限付き操作

`scene` は `running / paused / transition / terminal / unknown` を区別します。全サンプルで終局を確認し、明確な終局証拠は残存する戦闘 UI より優先します。戦闘確認後、戦闘内の `transition` は `input_block_reason=pause_verification_pending` として入力を禁止し、直ちに恒久的な終局隔離とは扱いません。復旧には明確な一時停止証拠を持つ新しい画像を連続 2 回取得する必要があり、実行中または遷移画像で回数をリセットします。実行ボタンの不在だけでは一時停止と判断しません。一時停止処理の最後に行う読み取り専用確認の時間枠は、3000 ms と ｢観測済みの最大フレーム取得・解析時間の 2 倍 + 1000 ms｣ の大きい方とし、2 フレームの確認とポーリングの時間を確保します。ただし常に命令の残り時間を上限とし、キャンセルで確認を停止します。不明のままなら入力禁止を維持します。

`battle_observe`、`battle_capture_evidence`、`battle_collect_terminal(request)` は入力なしで復旧証拠を収集できます。`battle_collect_terminal.duration_ms` は 100–8000 かつ `timeout_ms` 以下です。`status=succeeded` は収集完了であり、一時停止の復旧や勝利ではありません。`pause_verified`、`session_input_blocked`、`terminal` を確認してください。ロック中に受け付けた変更命令は、その観測で復旧しても実行しません。新しい証拠を確認して新規操作を要求し、必要なら手動停止の引き継ぎを明示的に許可します。過去の操作の再実行や結果の書き換えはありません。戦闘後の不明／非戦闘画面、終局／矛盾証拠、速度読み戻し失敗は `input_block_reason=terminal_or_safety_quarantine` の恒久隔離を維持し、一時停止証拠でも解除できません。`session_terminal` は結果と画像パスを保持し、黒画面で結果を消さず、勝敗の矛盾は unknown にします。Core の追加命令は `set_speed`、`finalize`、`prepare`、`bind` です。準備は `preparation_only=true` で初期化し、同じコントローラーで紐付けます。

一時停止認識はまず `PAUSE` の完全一致を要求します。0.9 に達しない場合、`[560,350,160,70]` の領域で中国版の `暂停中` を確認します。中国語は単一枠の完全な語、または 1 回の OCR で得た空間順の同一行の 2～3 断片に対応し、各断片に 0.9 以上を要求します。枠の間隔と重なりは有限の範囲に制限し、欠字、余分な接頭辞／接尾辞、別行、過大な重なり、領域外の枠を拒否します。フレームをまたいで結合しません。単一枠の source は `BattleControlPausedLocalized`、分割枠は `BattleControlPausedLocalizedSplit` です。`pause_evidence` は `fragments`、全文、断片の最低 `score`、結合枠、フレーム ID／時刻、`accepted`／`rejection_reason` を保持します。`PAUSEX` は代替証拠になりません。実行中ボタンと終局判定を優先し、隔離解除には引き続き連続 2 フレームが必要で、一時停止の所有権は自動取得しません。

一時停止認識に失敗した場合、`pause_diagnostics` は同一フレームの ID／時刻、設定 ROI、必要スコア、前処理設定、null を許す一致結果を保持します。`localized.candidates` と正式な中国語ルールは対象文字フィルターを外した 1 回の OCR を共有し、`candidate_source=same_frame_localized_pause_rule_ocr` とします。`localized.fragment_rule` に受理または拒否理由を記録し、ルールが受理した場合のみ一時停止証拠になります。個別候補だけでは証拠になりません。英語の診断候補は引き続き別のフィルターなし OCR から取得し、正式な一致を代替しません。null は低スコアの測定値ではありません。ネイティブ幾何回帰は報告の重なった枠を扱い、合成文字回帰は正式な OCR 経路を使用しますが、ゲームのフォント、エフェクトによる遮蔽、他サーバーへの対応を証明しません。配置後の実 OCR、1x／2x、戦闘全体の確認は引き続き実機で必要で、速度の閾値は変更していません。

`skill_ready` は true または null で、分類器の否定を確認済みクールダウンと扱いません。`skill_evidence` は同一フレームの ID、時刻、ROI、基点、分類スコアと確率を残します。`probabilities` は `c, n, y` 順で、`score` は最大の分類スコアであり、必ずしも使用可能クラス `y` のスコアではありません。`y` が最大かつ 0.9 以上の場合のみ true を返します。発動段階／弾薬／クールダウンは不明のままで構いません。スコアは校正済み正解率ではありません。`asst.log` の `Battle frame` / `Battle command result`、`battle-control/<session>/` の PNG、`operations.sqlite3` を保存してください。合成回帰は実 OCR、ROI、ボタンの対応確認とは別で、実機再確認が必要ですが、ローカル画像をクラウド開発の必須入力にはしません。

接続後、attach 前の `battle_observe_preparation(stage_name, mode)` は手動で開いた編成画面を入力なしで読みます。名前、候補 ID、名前の矩形と証拠を返し、選択スキル、育成、サポートの識別は不明で、詳細画面を開きません。最新の結果を `battle_attach(..., preparation_id=..., confirm_preparation_unchanged=true)` で紐付けます。これは呼び出し側の再確認であり、連続観測ではありません。編成／スキル／ステージ／アカウント変更後は `battle_invalidate_preparation` と再取得が必要です。再取得、紐付け消費、切断後は旧結果を使えず、現場の識別不一致は `decision_context.preparation` に記録します。

ネイティブの一括確認には、別の**変更型**ツール `battle_inspect_preparation` を 1 回呼び出します。

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

Core はレベル表示から配置済み枠を検出し、名前を読めない項目も残し、レベル OCR と昇進 1／2 の肯定的なテンプレートを読み取ります。その後、一意に識別した通常メンバーの顔を順に開き、詳細の名前を確認してスキルタブだけを開き、回数制限付きで一覧をスクロールします。完全な順序付き一覧内の選択マークが一意な場合だけ `selected_skill=1|2|3` を返します。`equipped_skill_id` にはさらに一意の識別と `battle_data.json` の順序付き `skillIds` が必要で、静的 ID だけで装備済みとは判定しません。選人カード、スキル選択肢、編成確定、開始ボタンは押しません。サポート詳細は未対応で、昇進テンプレート未検出は null のまま、**昇進 0 と推定しません**。レベル失敗も 1 に置き換えません。空枠はメンバーではなく、レベル表示の不検出は編成全体の完全性を証明しません。

`snapshot.scan_status=complete|partial`、各メンバーの `fields`（値、出典、確認状態、理由、ROI、フレーム証拠）、`evidence_frames`、`formation_restored`、`roster_unchanged`、`preparation_valid` を確認してください。complete の対象は検出済みメンバーだけで、partial は完全な構成を意味しません。PNG パスと取得時刻は元フレームに対応し、選択スキルにはフレーム間のスクロール順序も付属します。既定期限は 120000 ms、**最大 180000 ms** で、通常命令の 30000 ms とは異なります。キャンセル／切断は Core に通知して有限の後処理を待ちます。名前の一致する確認済み詳細画面だけ、元の期限内に 1 回戻れます。不明画面は盲目的に閉じません。ワーカー失敗時の復帰状態は不明です。同じ操作 ID は同じ引数だけに使用し、再実行要求は元の証拠を返すだけで移動や無効化済み結果の再有効化は行いません。attach 前の永続結果は `battle_status.preparation_session_id` で `battle_get_operation` から取得できます。

有効な最新結果だけを既存の明示的な再確認付き attach に使用できます。新しい観測は旧紐付けを無効化し、画面／編成の変化、証拠保存失敗、中断した走査は有効な紐付けを作りません。`decision_context.preparation.observed_fields` はネイティブ観測を入力された構成と分離して保持し、不一致を記録しますが双方を上書きしません。これは戦闘前の測定であり、アカウント／ステージ／構成の連続確認や戦闘中の測定ではありません。既存リソースを使う認識器には合成 Debug Core 回帰がありますが、**実機の動作確認ではありません**。実際の画面配置と選択マークの互換性はローカルで確認が必要です。

### オフラインのユニット情報と所有個体

`battle_get_unit_info(game_id)` は端末接続・情報 provider なしで同梱の `resource/unit_info.json` を読み、`unit`・`source`・`resource_sha256`・`scope` を返します。TOKEN／TRAP／オペレーター分類、効果、基礎属性キーフレームと範囲 ID、スキル参照・定義、素質 blackboard、条件付き `tokenKey`／`overrideTokenKey` ID 関係を保持します。静的な表の値は現場の `cost`／`available`／`cooling` や実際の装備測定ではありません。ユーザー設定で視覚 ID の曖昧さを解消しません。

`base_range_definitions` は `base_range_coordinate_system` のゲーム相対行／列座標を保持し、実測 MAA マスではありません。

能力は `value=true|false|null`・`source`・`reason`・`game_data_value`・`live_verified` を持ちます。レビュー済み adapter 値は制限付き操作の仕様であり、エンジンの方針や実機検証の証明ではありません。`execution_support` は入力試行のみ／未対応の操作を別に示します。unknown は許可ではありません。最初の召喚 adapter は医療ドローン `token_10000_silent_healrb` の通常ドラッグと `direction="None"` です。他の TOKEN／TRAP は未知として拒否し、同マス共存と召喚物スキルは未対応です。

`battle_step(action="deploy", unit_id=..., card_id=...)` を使います。`game_id` は種類、`card_id` は観測カード、`instance:<operation_id>` は所有する配置試行です。名前の一意性より先に具体的カードを選ぶので同名カードを区別できますが、ゲーム ID は一意でなければなりません。再開後に身元・配置・外観・利用可否・30 秒の鮮度を再確認します。`--allow-summon` 省略時は対応済み adapter を許可し、旧オプションは明示的な制限として警告付きで保持します。未対応操作を許可しません。ネイティブの `allowed_summons=[]` は全召喚を拒否し、フィールド省略で対応済みカードを有効化します。

C++ 型で presence・位置証拠・操作状態を分離し、入力やコスト／再配置状態の変化では配置を確定しません。変更操作の有界詳細確認は 2 フレームの名前／アイコン証拠を記録しますが、この個体の存在や選択マスを証明しません。未知個体は要求マスを予約します。`battle_step(action="retreat", unit_id="instance:...")` は能力を確認し、同種・隣接個体の曖昧さを拒否して身元と正のボタン証拠を確認します。**実画像に基づく選択マスの anchor と明確な離場規則は未実装なので、anchor 未確認では撤退ボタンを押さず、不明な占有を解放しません。** ボタン不検出は `selection_unverified` で永久撤退不可とは推定せず、明示的 false のみ `not_retreatable` です。受動観測はクリック／再開せず、時間や単一フレーム不検出で消滅を断定しません。確認済み終局では離場を記録できます。再送は日誌・画像を保持して再入力せず、再起動／途中参加の復元は未対応です。

正の詳細画像は選択した種類の存在（`selected_kind_present`）だけを示し、この配置の成功を証明しません。マス anchor なしでは所有個体の `presence` は unknown です。撤退アイコン一致は `retreat_button_detected` で、有効状態の証明ではありません。可用性規則がないので `retreat_button_available` は null です。

旧 `battle_deploy` は任意の `card_id` を受け、旧配置／撤退／スキル入口も同じ有界事前確認・停止処理を通ります。開始時は実行中戦闘が必要で、能力・個体・位置検証を迂回できません。選択マス anchor がない通常オペレーターのスキル／撤退も未確認履歴で実行せず拒否します。

TL／TL-EX の ID は `act55side_*`（例 `act55side_ex01`、`TL-EX-1` ではありません）です。｢自爆榴弹｣（爆発するステージ装置）`trap_341_dsjtzd` はオフライン照会可能で、基本コスト 20、再配置 20 秒、範囲 `x-4`、スキル記載なし、素質 blackboard のスタン 5 秒です。**再配置時間は爆発のカウントダウンではありません。** 原表はドラッグ／撤退方針を証明せず、爆発・確実な消滅・翻板変更の実画像規則がないので動的 overlay は unknown／unsupported、TL 配置は `dynamic_terrain_unknown` です。推定時間で静的地図を変更せず、TL の完全ループ・実機合格を宣言しません。

Debug の `res_updater` をビルドし、固定版のローカルキャラクター／スキル表から再生成します。

```bash
python tools/ResourceUpdater/generate_unit_info.py \
  --updater build-mcp/bin/Debug/res_updater \
  --input path/to/excel --output resource/unit_info.json
```

`tools/ResourceUpdater/unit_info_source.json` はリポジトリ・commit・地域・キャラクター／昇格キャラクター／スキル／範囲の 4 入力 SHA-256 を固定し、wrapper が検証後に唯一の C++ generator を呼びます。実行時のダウンロード／Wiki は不要です。生成 JSON は 1 レコード 1 行で Prettier の対象外です。通常更新は入力ディレクトリに `unit_info_source.json` があるときのみこの補助ファイルを再生成します。宣言ソース更新時は再生成・検証が必要で、ハッシュはインストール済みゲームとの互換性を証明しません。外部 raw ステージ／敵スナップショットは別設定で同梱されません。

`battle_set_speed(request)` は `session_id`、`operation_id`、`multiplier=1|2`、`timeout_ms` を受け付けます。明確な停止中だけ速度を読み、必要なら 1 回切り替えて再確認し、前後の証拠を返します。停止所有権を変えず、自動再開せず、2 倍の重複指定で 1 倍に戻りません。速度不明時はクリックせず、変更後に確認できなければ入力をロックします。加速の判断はホストだけが行います。待機はホスト時間で、観測済み取得コストから後処理予算を確保し、不足なら再開を拒否します。`cleanup_reserve_ms`、`resumed_interval_upper_bound_ms`、`deadline_margin_ms` は厳密なゲーム時間を保証しません。

1X は 0.9 以上の OCR を優先します。不足する場合、`BattleControlSpeedOneTemplate` が同じ速度領域で既存の `BattleSpeedUp.png` を再利用し、同じく 0.9 以上を要求します。`one_x_ocr_evidence` と `one_x_template_evidence` は個別結果、`one_x_evidence` は採用結果です。2X は引き続き `BattleSpeedUpCheck` を使用し、1X／2X の証拠が矛盾すれば unknown を維持します。2X が見つからないだけでは 1X と推定しません。テンプレート回帰はレポートのゲーム画像の検証ではありません。

## 固定された生ステージデータ

`--intel-config` の `provider="raw"` は信頼できるローカルファイル専用で、自動ダウンロードしません。設定項目は `root`（絶対ディレクトリ）、`repository`、`commit`（40 桁）、`data_version`、`region`、`stages`、`enemy_database` です。`stages` は stage ID を `{"path":"relative/path","sha256":"64-character hash"}` に対応させ、`enemy_database` も同じ形式です。各ファイルは 64 MiB 以下で SHA-256 を検証し、ルート外を拒否します。

紐付け済みセッションで `intel_query(..., complete_stage=true)` を使います。上起点のマップと下起点の経路を別々に変換して `maa_grid` を出力し、待機チェックポイントを移動扱いしません。SPAWN action ごとに 1 レコードを作り、count、delay、interval、隠しグループ、ブロック項目を保持します。敵のレベル継承、定義フラグ、ステージ上書きを処理し、Key/Value と key/value に対応します。不明な機構や非 SPAWN actions を空集合にしません。`mode_modifiers` は難易度 mask の選択のみで、ルーン解釈済みとは扱わず、`alignment=unknown` です。`mode=normal|challenge` は難易度で、演習／実戦ではありません。外部 provider に mode を渡すのは schema に宣言がある場合だけで、キャッシュも mode 別です。ハッシュや版の宣言はゲーム版との一致や独立 provider の互換性を証明せず、外部 provider のデータパックはサービスと共に再配布しません。

上の再配布しないという記述は外部 raw ステージ／敵入力のみを指し、前述のオフラインユニット情報は別途同梱します。

## Core プロトコル

`AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})` で初期化し、`AsstStart` の後に `SubTaskExtraInfo` の `what="BattleControlReady"`（プロトコルバージョン 1）を待ちます。`AsstSetTaskParams` で `session_id`、`command_id`、`action`、`args`、`timeout_ms` を送信し、`what="BattleControlResult"` を待ちます。同時に受け付けるコマンドは 1 件だけです。

アクションは `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused`、`step` です。`step` は事前検証済みの戦術操作を 1 つ受け付け、直後に撮影して一時停止を要求し、確認後に新しい一時停止画像を返します。端末 I/O または MCP プロセス自体が失敗した場合の一時停止は保証できません。引数更新はキューへの追加のみで、端末操作は Core のタスクスレッドで実行します。C ABI 単体では低レベル I/O の終了期限を保証できないため、ネイティブクライアントには外部 watchdog と重複排除が必要です。MCP サーバーはプロセス単位の watchdog を提供します。反復実験と最適化は今後の対応です。

`identify` は省略可能な厳密な boolean 引数 `args.adopt_manual_pause`（既定値 false）を受け付けます。明示的な引き継ぎは認識済みの停止を管理下に置くだけで、停止を切り替えたり、この認識操作による再開を許可したりするものではありません。

`step` はユニット引数なしの `args.action="wait"` と `args.duration_ms` にも対応します。実行中は `AsstSetTaskParams` に `{"session_id":"…","cancel_command_id":"…"}` を送信できます。対応するコマンドに取消信号を設定するだけで、端末操作や 2 件目の結果は生成しません。遅延した取消は次のコマンドに影響せず、元のコールバックが後処理結果を返します。MCP は元の `operation_id` で保存と重複排除を行います。
