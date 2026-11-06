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

実際の Debug Core によるタスク登録、引数の拒否、コールバック、マップ投影の確認も端末なしで実行できます。

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_native.py -q
```

PowerShell では先に `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"` を設定し、同じ pytest コマンドを実行します。リソースが別の場所にある場合は、その親を `MAA_MCP_RESOURCE_DIR` に設定します。環境変数がなければ明示的にスキップされます。オフラインテストの成功は実機動作の保証ではありません。

Windows のソースからの Debug ビルドでも、先に `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install` を実行します。前提条件は Debug パッケージと同じです。

## 起動とクライアント設定

同じ端末を操作する別の MAA/GUI タスクを停止し、固定マップの戦闘を手動で開始してください。初版は契約選択、ステージ開始、勝利判定、多段階マップのカメラ移動、接続前に配置済みのオペレーターの復元を行いません。

同じ `--user-dir` はプロセス間ロックで保護されます。同じ端末には常に同じディレクトリを使用してください。別ディレクトリのサーバーや外部 GUI による操作は防げません。

個体ごとの識別が実装されるまでは召喚物や装置の配置を明示的に拒否します。同名の複数ユニットを 1 人のオペレーターとして扱うことはできません。

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

`outcome_unknown` は MCP の `isError=true` になります。`helper_returned=true` でもゲーム内での成功は保証されません。再撮影して確認し、無条件に再試行しないでください。配置位置の情報源は `action_history_unverified`、敵の状態は null です。モデルによる状態の直接上書きには対応しません。一時停止要求は停止確認と同じではなく、再開は本サーバーが要求した停止に限定されます。

`timeout_ms` は 1–30000 です。期限切れやキャンセル時は worker を終了し、セッションを無効化して不明な結果を保存します。`battle_close` は操作を中断しますが、**ゲームを一時停止・終了しません**。`battle_get_operation` で永続化された結果を取得できます。ログと `operations.sqlite3` は `--user-dir` に保存され、明示的な撮影画像は各戦闘につき直近 128 枚を保持します。古いセッション、ログ、データベースは定期的に整理してください。

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

## Core プロトコル

`AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})` で初期化し、`AsstStart` の後に `SubTaskExtraInfo` の `what="BattleControlReady"`（プロトコルバージョン 1）を待ちます。`AsstSetTaskParams` で `session_id`、`command_id`、`action`、`args`、`timeout_ms` を送信し、`what="BattleControlResult"` を待ちます。同時に受け付けるコマンドは 1 件だけです。

アクションは `observe`、`capture`、`map`、`identify`、`deploy`、`retreat`、`skill`、`set_paused` です。引数更新はキューへの追加のみで、端末操作は Core のタスクスレッドで実行します。C ABI 単体では低レベル I/O の終了期限を保証できないため、ネイティブクライアントには外部 watchdog と重複排除が必要です。MCP サーバーはプロセス単位の watchdog を提供します。操作後の自動成功確認、反復実験、最適化は今後の対応です。
