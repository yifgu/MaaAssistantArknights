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

召喚物の配置は信頼できる起動時許可リストに限定し、個体別に記録します。未許可の装置と召喚物のスキル／撤退は引き続き拒否します。詳細は後述します。

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

全コマンドの `timeout_ms` 上限は 30000 です。通常の期限切れ／キャンセルでは worker を終了してセッションを無効化し、不明な結果を保存します。step は停止後処理を待ちますが、端末故障時の停止は保証しません。`battle_close` は通常コマンドを中断、または step の後処理を待ち、それ自体では停止・終了しません。`battle_get_operation` は永続結果を読み、保存失敗時は明示的に報告して以後のコマンドを拒否します。`--user-dir` にログと `operations.sqlite3` を保存し、通常画像は直近 128 枚、重要証拠は別途保持します。手動整理が必要です。

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

`scene` は `running / paused / transition / terminal / unknown` を区別します。全サンプルで終局を確認し、明確な終局証拠は残存する戦闘 UI より優先します。戦闘確認後、戦闘内の `transition` は `input_block_reason=pause_verification_pending` として入力を禁止し、直ちに恒久的な終局隔離とは扱いません。復旧には明確な一時停止証拠を持つ新しい画像を連続 2 回取得する必要があり、実行中または遷移画像で回数をリセットします。実行ボタンの不在だけでは一時停止と判断しません。一時停止処理の最後に命令期限内で最大 3000 ms の読み取り専用確認を行い、キャンセルで確認を停止します。不明のままなら入力禁止を維持します。

`battle_observe`、`battle_capture_evidence`、`battle_collect_terminal(request)` は入力なしで復旧証拠を収集できます。`battle_collect_terminal.duration_ms` は 100–8000 かつ `timeout_ms` 以下です。`status=succeeded` は収集完了であり、一時停止の復旧や勝利ではありません。`pause_verified`、`session_input_blocked`、`terminal` を確認してください。ロック中に受け付けた変更命令は、その観測で復旧しても実行しません。新しい証拠を確認して新規操作を要求し、必要なら手動停止の引き継ぎを明示的に許可します。過去の操作の再実行や結果の書き換えはありません。戦闘後の不明／非戦闘画面、終局／矛盾証拠、速度読み戻し失敗は `input_block_reason=terminal_or_safety_quarantine` の恒久隔離を維持し、一時停止証拠でも解除できません。`session_terminal` は結果と画像パスを保持し、黒画面で結果を消さず、勝敗の矛盾は unknown にします。Core の追加命令は `set_speed`、`finalize`、`prepare`、`bind` です。準備は `preparation_only=true` で初期化し、同じコントローラーで紐付けます。

一時停止認識に失敗した場合、`pause_diagnostics` は同一フレームの ID／時刻、設定 ROI、必要スコア、null を許す一致結果を保持します。OCR では前処理設定と、同じ画像で対象文字フィルターを外した診断候補も返しますが、候補は一時停止の証拠ではありません。null は低スコアの測定値ではありません。一時停止／速度の閾値は変更せず、配置後の実 OCR、1x／2x、戦闘全体の確認は引き続き実機で必要です。

`skill_ready` は true または null で、分類器の否定を確認済みクールダウンと扱いません。`skill_evidence` は同一フレームの ID、時刻、ROI、基点、分類スコアと確率を残します。発動段階／弾薬／クールダウンは不明のままで構いません。スコアは校正済み正解率ではありません。`asst.log` の `Battle frame` / `Battle command result`、`battle-control/<session>/` の PNG、`operations.sqlite3` を保存してください。合成回帰は実 OCR、ROI、ボタンの対応確認とは別で、実機再確認が必要ですが、ローカル画像をクラウド開発の必須入力にはしません。

接続後、attach 前の `battle_observe_preparation(stage_name, mode)` は手動で開いた編成画面を入力なしで読みます。名前、候補 ID、名前の矩形と証拠を返し、選択スキル、育成、サポートの識別は不明で、詳細画面を開きません。最新の結果を `battle_attach(..., preparation_id=..., confirm_preparation_unchanged=true)` で紐付けます。これは呼び出し側の再確認であり、連続観測ではありません。編成／スキル／ステージ／アカウント変更後は `battle_invalidate_preparation` と再取得が必要です。再取得、紐付け消費、切断後は旧結果を使えず、現場の識別不一致は `decision_context.preparation` に記録します。

通常のドラッグ配置が確認済みの召喚カードだけ、起動時に `--allow-summon GAME_ID` を繰り返し指定して許可します。`battle_step(action="deploy")` には通常引数に加えて観測した `card_id` が必要です。一意、利用可能、位置不変、取得後 30 秒以内を要求します。毎回独立した `instance:<operation_id>` を作り、同名を上書きしません。位置と存続は未確認の操作履歴で、不明な消滅から占有を自動解放しません。個体スキル／撤退、曖昧なカード、未許可装置は拒否し、helper の返却値を配置成功とは扱いません。

`battle_set_speed(request)` は `session_id`、`operation_id`、`multiplier=1|2`、`timeout_ms` を受け付けます。明確な停止中だけ速度を読み、必要なら 1 回切り替えて再確認し、前後の証拠を返します。停止所有権を変えず、自動再開せず、2 倍の重複指定で 1 倍に戻りません。速度不明時はクリックせず、変更後に確認できなければ入力をロックします。加速の判断はホストだけが行います。待機はホスト時間で、観測済み取得コストから後処理予算を確保し、不足なら再開を拒否します。`cleanup_reserve_ms`、`resumed_interval_upper_bound_ms`、`deadline_margin_ms` は厳密なゲーム時間を保証しません。

## 固定された生ステージデータ

`--intel-config` の `provider="raw"` は信頼できるローカルファイル専用で、自動ダウンロードしません。設定項目は `root`（絶対ディレクトリ）、`repository`、`commit`（40 桁）、`data_version`、`region`、`stages`、`enemy_database` です。`stages` は stage ID を `{"path":"relative/path","sha256":"64-character hash"}` に対応させ、`enemy_database` も同じ形式です。各ファイルは 64 MiB 以下で SHA-256 を検証し、ルート外を拒否します。

紐付け済みセッションで `intel_query(..., complete_stage=true)` を使います。上起点のマップと下起点の経路を別々に変換して `maa_grid` を出力し、待機チェックポイントを移動扱いしません。SPAWN action ごとに 1 レコードを作り、count、delay、interval、隠しグループ、ブロック項目を保持します。敵のレベル継承、定義フラグ、ステージ上書きを処理し、Key/Value と key/value に対応します。不明な機構や非 SPAWN actions を空集合にしません。`mode_modifiers` は難易度 mask の選択のみで、ルーン解釈済みとは扱わず、`alignment=unknown` です。`mode=normal|challenge` は難易度で、演習／実戦ではありません。外部 provider に mode を渡すのは schema に宣言がある場合だけで、キャッシュも mode 別です。ハッシュや版の宣言はゲーム版との一致や独立 provider の互換性を証明せず、データはサービスに再配布しません。

## Core プロトコル

`AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})` で初期化し、`AsstStart` の後に `SubTaskExtraInfo` の `what="BattleControlReady"`（プロトコルバージョン 1）を待ちます。`AsstSetTaskParams` で `session_id`、`command_id`、`action`、`args`、`timeout_ms` を送信し、`what="BattleControlResult"` を待ちます。同時に受け付けるコマンドは 1 件だけです。

アクションは `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused`、`step` です。`step` は事前検証済みの戦術操作を 1 つ受け付け、直後に撮影して一時停止を要求し、確認後に新しい一時停止画像を返します。端末 I/O または MCP プロセス自体が失敗した場合の一時停止は保証できません。引数更新はキューへの追加のみで、端末操作は Core のタスクスレッドで実行します。C ABI 単体では低レベル I/O の終了期限を保証できないため、ネイティブクライアントには外部 watchdog と重複排除が必要です。MCP サーバーはプロセス単位の watchdog を提供します。反復実験と最適化は今後の対応です。

`identify` は省略可能な厳密な boolean 引数 `args.adopt_manual_pause`（既定値 false）を受け付けます。明示的な引き継ぎは認識済みの停止を管理下に置くだけで、停止を切り替えたり、この認識操作による再開を許可したりするものではありません。

`step` はユニット引数なしの `args.action="wait"` と `args.duration_ms` にも対応します。実行中は `AsstSetTaskParams` に `{"session_id":"…","cancel_command_id":"…"}` を送信できます。対応するコマンドに取消信号を設定するだけで、端末操作や 2 件目の結果は生成しません。遅延した取消は次のコマンドに影響せず、元のコールバックが後処理結果を返します。MCP は元の `operation_id` で保存と重複排除を行います。
