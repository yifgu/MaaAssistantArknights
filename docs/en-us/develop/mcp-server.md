---
icon: mdi:robot
order: 20
---

# Battle MCP Server

`src/MaaMcpServer` provides a local stdio MCP server with MaaCore isolated in a worker process. Requires Python 3.11+, uv, and Core built from this checkout; older builds lack the `BattleControl` task. This is experimental battle control, not a Contingency Contract optimizer.

## Build and install

Run from the repository root. Prepare the compiler and CMake using the [Linux build guide](./linux-tutorial.md) or your platform's guide. On Windows, use a Visual Studio developer terminal.

```bash
git submodule update --init --recursive src/MaaUtils
python tools/maadeps-download.py
cmake --preset linux-x64 -B build-mcp -DBUILD_WPF_GUI=OFF -DINSTALL_RESOURCE=ON
cmake --build build-mcp --config Debug --target MaaCore -j 2
cmake --install build-mcp --config Debug --prefix "$PWD/build-mcp/install"
uv sync --project src/MaaMcpServer
```

Replace `linux-x64` with `windows-x64`, `macos-arm64`, or `macos-x64` as appropriate. Do not share a build directory between platforms. The installation must contain Core, its runtime dependencies, and `resource/`. `--resource-dir` can separately specify the parent of `resource/`.

## Local checks

Offline unit tests, real MCP stdio handshakes, and a fixture intelligence server need no device:

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

The actual Debug Core task-registration, input-validation, callback, and map-projection smoke test also needs no device:

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_native.py -q
```

In PowerShell, first set `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`, then run the same pytest command. Set `MAA_MCP_RESOURCE_DIR` to the resource parent if it differs. Without the variable this test explicitly skips; passing offline checks does not establish in-game correctness.

## Launch and client configuration

Stop other MAA/GUI tasks controlling the same device, then manually enter a fixed-map battle. This version does not select contracts, enter stages, recognize victory, move a multi-stage camera, or reconstruct operators already deployed before attachment.

A cross-process control lock protects each `--user-dir`. Always use the same directory for the same device; this lock cannot prevent another directory's server or an external GUI from controlling it.

Summon and device deployment is explicitly rejected until instance-level identity tracking exists; multiple same-name units must not be controlled as one operator.

```bash
uv run --project src/MaaMcpServer maa-battle-mcp \
  --core-dir "$PWD/build-mcp/install" \
  --adb-path /absolute/path/to/adb \
  --address 127.0.0.1:5555 \
  --user-dir "$PWD/build-mcp/session"
```

The process waits for an MCP client; it does not serve a web page or automatically connect. Replace paths with absolute paths in your client's configuration; use `/` or escaped backslashes in Windows JSON:

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

Call `battle_connect`, `battle_attach(stage_name="main_01-07")`, `battle_capture_evidence`, then `battle_observe`. The stage ID must match the screen: binding is caller-supplied, not recognized. `battle_get_map` returns static tiles and both screen projections. Unknown names are null. `battle_identify` actively opens details and requests a pause; inspect evidence afterward and explicitly resume with `battle_set_paused(paused=false)`.

Deployment request, substituting the returned session and observed unit IDs; tool arguments contain a `request` object:

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

`battle_retreat` and `battle_use_skill` use the same request without `location` or `direction`. Use a new `operation_id` for each new action. Identical retries return the saved result; changed parameters under the same ID are rejected. Insufficient cost or cooldown fails immediately instead of waiting indefinitely. Evidence returns a PNG and observation from the same frame for the client's multimodal model; the server does not invoke a cloud model.

`outcome_unknown` sets MCP `isError=true`; even `helper_returned=true` does not prove in-game success. Inspect a new capture instead of blindly retrying. Positions have `action_history_unverified` provenance, enemies are null, and model-supplied state overwrites are not supported. A requested pause is not a confirmed pause; resume is guarded to pauses requested by this server.

`timeout_ms` is 1–30000. Timeout or cancellation terminates the worker, invalidates the session, and persists an unknown outcome. `battle_close` interrupts control but **does not pause or exit the game**. Query durable results with `battle_get_operation`. Logs and `operations.sqlite3` live in `--user-dir`; each battle retains its latest 128 explicit captures. Periodically clean old sessions, logs, and the database yourself.

## Optional intelligence

Install the provider and prepare its data snapshot separately, then pass `--intel-config /absolute/intel.json`. This is trusted local startup configuration, not a model-controlled program launcher:

```json
{
  "provider": "intelligence",
  "command": "uv",
  "args": ["run", "--project", "/absolute/arknights-mcp", "arknights-mcp", "serve", "--transport", "stdio"],
  "region": "cn"
}
```

For PRTS use `{"provider":"prts","command":"prts-mcp","region":"cn"}`; the adapter requests stdio and structured output. `intel_query` supports `stage/operator/enemy`. Intelligence expects game IDs; PRTS expects stage IDs or Chinese operator/enemy names. Intelligence `routes_page` and `spawns_page` are 1-based, with 50 rows per page. Results preserve provenance, limitations, and pagination and are cached for five minutes. PRTS stage queries return enemies, not tiles/routes/waves; they never silently substitute CN data for another region. Static intelligence is not live state, and external maps are not automatically merged or calibrated.

## Core protocol

Initialize with `AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})`, call `AsstStart`, and await `SubTaskExtraInfo` with `what="BattleControlReady"` (protocol version 1). Submit `session_id`, `command_id`, `action`, `args`, and `timeout_ms` through `AsstSetTaskParams`; await `what="BattleControlResult"`. Only one command is accepted at a time; wait for its callback before submitting another.

Actions are `observe`, `capture`, `map`, `identify`, `deploy`, `retreat`, `skill`, and `set_paused`. Parameter updates only enqueue work; device access stays on the Core task thread. Native clients must supply their own external watchdog and deduplication: the C ABI alone cannot guarantee a blocking I/O deadline. The MCP server supplies that process-level watchdog. Automatic postcondition confirmation, experiments, and optimization are future work.
