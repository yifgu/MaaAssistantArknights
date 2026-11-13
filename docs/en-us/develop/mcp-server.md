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

## Use GitHub Actions artifacts

In your repository, open **Actions → Release Pipeline → Run workflow**, select the MCP branch, enable `mcp_only`, and choose `mcp_configuration`:

- `RelWithDebInfo`: the default complete runtime bundle; no local Python, uv, ADB, or Visual Studio installation is required.
- `Debug`: a development/test bundle. Linux runs directly; Windows requires a locally installed, non-redistributable Debug CRT.

Both configurations first build Debug Core and run the full test suite, then package the selected configuration. Neither builds the GUI nor publishes a release. Equivalent command:

`mcp_platform` defaults to `all`; select `win-x64` or `linux-x64` to build only the platform you need and avoid duplicate runner usage.

```bash
gh workflow run ci.yml --ref <branch> -f mcp_only=true -f mcp_configuration=RelWithDebInfo
```

Relevant PRs run the same `Battle MCP` job. `.github/workflows/mcp-build.yml` (**Battle MCP Bundle**) is shared by the regular and Nightly pipelines. Use the existing Release Pipeline to dispatch it before the new workflow reaches the default branch. Manual MCP builds have a separate concurrency group so a PR build cannot cancel them.

Download `MAAMcp-win-x64-RelWithDebInfo` or `MAAMcp-linux-x64-RelWithDebInfo` from the successful run's **Artifacts** (or the corresponding `-Debug` artifact), then extract the enclosed `.zip` or `.tar.gz`. Included: `core/` (Core, native dependencies, resources, ADB ControlUnit), `python/` (standalone Python and all locked runtime/test dependencies), `adb/`, launchers, source/tests, wheel/sdist, dependency licenses, and `BUILD-INFO.json` with the source commit and run URL. SHA-256 checksums accompany the archives. The Windows runtime bundle includes the redistributable VC++ CRT. No checkout, compilation, or online Python package installation is needed. Linux still requires a compatible x64 OS and system glibc; this is not a container or an operating system.

On Windows x64, run from the download directory:

```powershell
Expand-Archive .\MAAMcp-win-x64-RelWithDebInfo.zip .\mcp-test
Set-Location .\mcp-test
.\run-mcp.cmd --check
.\run-mcp.cmd --address 127.0.0.1:5555
```

On Linux x64, run from the download directory:

```bash
mkdir mcp-test
tar -xzf MAAMcp-linux-x64-RelWithDebInfo.tar.gz -C mcp-test
cd mcp-test
./run-mcp.sh --check
./run-mcp.sh --address 127.0.0.1:5555
```

`--check` loads bundled ADB, Python dependencies, and Core/resources without connecting a device. Normal startup is stdio and waits for MCP input; there is no web page. Configure your client's `command` as the bundled `python/python.exe` on Windows or `python/bin/python3` on Linux, with `args`: `["-I","-B","/absolute/mcp-test/run_mcp.py","--address","127.0.0.1:5555"]`. Use absolute paths throughout. State defaults to `state/` in the bundle; override with `--user-dir` for another writable directory.

For the full offline suite, use a Debug bundle with `run-mcp.cmd --self-test` or `./run-mcp.sh --self-test`. No device is connected. On Windows, install Visual Studio 2026/Build Tools with the C++ desktop workload and Windows SDK, then run `powershell -NoProfile -ExecutionPolicy Bypass -File tools/PrepareMcpRuntime.ps1 -CoreDir core`. **The Debug CRT cannot be redistributed; the normal VC++ Redistributable cannot replace it.** The script copies only local development DLLs; do not redistribute the modified directory. CI tests in a separate Debug directory and excludes Debug CRT DLLs from artifacts. `RelWithDebInfo` bundles reject `--self-test` to avoid misleading results from disabled assertions.

Follow the connection and map-verification steps below for in-game use. Do not mix older Core/resources or tests from another commit. External intelligence programs and data snapshots are optional plugins, not server runtime dependencies, and require separate setup. Neither the game/emulator nor automatic cloud-model access is included.

Windows Debug is ABI-incompatible with MaaFramework's Release ControlUnit, so that optional DLL is excluded and `--touch-mode MaaFwAdb` is rejected. Use the default `maatouch`, `adb`, or `minitouch`, or choose the complete `RelWithDebInfo` bundle.

## Local checks

Offline unit tests, real MCP stdio handshakes, and a fixture intelligence server need no device:

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

The actual Debug Core task-registration, input-validation, callback, and map-projection smoke tests also need no device. Synthetic-frame tests run the real Core with a local fixture transport instead of ADB to cover paused identification, cancellation, timestamp order, PNG association, and replay:

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest \
  src/MaaMcpServer/tests/test_native.py src/MaaMcpServer/tests/test_battle_control.py -q
```

In PowerShell, first set `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`, then run the same pytest command. Set `MAA_MCP_RESOURCE_DIR` to the resource parent if it differs. Without the variable this test explicitly skips; passing offline checks does not establish in-game correctness.

Windows Debug builds from source also require `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install` before testing, with the same prerequisites as the Debug bundle.

The worker uses a separate subprocess with private stdin/stdout pipes, not the MCP streams. stderr records its PID and `worker_started`, `core_load`, `resource_load`, `core_create`, `ready`, and request phases. Startup/connection timeouts name the last phase rather than claiming an unknown battle outcome. Timeout or cancellation still terminates the worker and releases the controller lease; interrupted battle commands remain `outcome_unknown` and are never automatically retried.

`battle_status` and `battle_connect` expose separate `project_version`, `build` (the adjacent `BUILD-INFO.json`), and `resources` (configured resource root and map-catalog SHA-256) fields. Missing manifests/catalogs are null; the MCP SDK version is not used as the project version. Successful connections also expose `core_version`. Attachment checks the catalog and map file by unique stage/level ID, rejecting display codes such as `TL-EX-1`. Missing data names the requested ID and resource root, without downloading or binding a fallback. The attachment's `map_source` includes the map-file SHA-256; provenance does not establish visual verification or cross-version compatibility.

For the optional live smoke test, explicitly set `MAA_MCP_NATIVE_CORE`, `MAA_MCP_LIVE_ADDRESS`, and `MAA_MCP_LIVE_STAGE`, then run `uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_stdio.py -k real_device`. Override resources, ADB, and connection configuration using `MAA_MCP_RESOURCE_DIR`, `MAA_MCP_ADB_PATH`, and `MAA_MCP_CONNECTION_CONFIG`. Stop other controllers and manually enter the fixed-map battle matching that unique ID first. The test only connects, attaches, captures, and closes; it never resumes, deploys, or abandons a battle. Missing prerequisites are explicit skips; offline fixture-worker success does not establish Windows in-game correctness.

## Launch and client configuration

Stop other MAA/GUI tasks controlling the same device, then manually enter a fixed-map battle. This version does not select contracts, enter stages, move a multi-stage camera, or reconstruct operators already deployed before attachment. Terminal recognition is limited to the title matching described below.

A cross-process control lock protects each `--user-dir`. Always use the same directory for the same device; this lock cannot prevent another directory's server or an external GUI from controlling it.

Summon deployment requires a trusted startup allowlist and uses separate instance records. Unapproved devices and summon skills/retreat remain rejected; see below.

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

For a fresh manual pause, the strict boolean `battle_identify` parameter `request.adopt_manual_pause=true` explicitly authorizes adoption without resuming; it defaults to false. Core requires positive pause evidence: absence of a running button is insufficient, and an introductory title cannot be adopted. Identification inspects cards while paused and returns `pause_verified`, `safe_state`, same-frame `snapshot`/`image_path` and PNG, including partial OCR. Write failures are explicit in `evidence_error`; cancellation or worker failure can prevent final evidence but never authorizes resume.

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

For a single tactical action, prefer `battle_step`, which performs preflight, any necessary resume, the action, a post-action capture, and pause verification in one Core command:

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

`action` can be `deploy`, `retreat`, `skill`, or `wait`; deployment also requires `location` and `direction`. Resuming a manual pause requires explicit `adopt_manual_pause=true` and fresh pause evidence. Each call executes at most one action, analyzes its boundary frame, then requests pause and returns fresh evidence with `pause_verified`. Cancellation notifies Core and waits for cleanup; worker failure may leave pause unknown. `battle_step.timeout_ms` defaults to 10000 and permits up to 30000; the caller's budget is never silently extended.

`captured_at_ms` records the host's Unix time in milliseconds immediately after image acquisition, before analysis; it is not the device's render time. `capture_time_snapshot` stays paired with `capture_image_path` (action boundary), and `return_time_snapshot` with `return_image_path` (later pause verification). MCP image content remains return frame first, boundary frame second. `observation_id` counts analyses, not captures, so the boundary observation can have the larger ID despite the earlier timestamp. Wall-clock adjustments can affect time differences; these timestamps are not a hard running-time budget. An identical operation replay preserves the original timestamps and paths; a fresh capture gets its own acquisition timestamp.

`battle_identify` returns `identified_count`, `unresolved_count`, `ambiguous_count`, and per-card `identity_reason`; blank-name cache entries no longer suppress detail OCR. For other actions, `outcome_unknown` sets MCP `isError=true`; neither `helper_returned=true` nor `action_returned=true` proves in-game success. Inspect evidence instead of blindly retrying. Positions have `action_history_unverified` provenance, enemies are null, and model-supplied state overwrites are not supported. A standalone pause request is not confirmation; resume is guarded to pauses requested or explicitly adopted by this server.

All commands cap `timeout_ms` at 30000. Ordinary timeout/cancellation terminates the worker and invalidates the session with an unknown outcome; a step waits for pause cleanup, which cannot guarantee pause during device failure. `battle_close` interrupts ordinary commands or waits for step cleanup; it does not itself pause or exit the game. `battle_get_operation` reads durable results; persistence failure explicitly blocks further commands in this service. Logs and `operations.sqlite3` live in `--user-dir`; ordinary images rotate after 128 captures, while critical evidence is retained separately and requires manual cleanup.

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

## Bounded waits and decision context

Wait without separate client-side resume and pause calls:

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

`duration_ms` is 100–8000 and must leave at least 1000 ms within `timeout_ms`; unit, location and direction arguments are forbidden. Duration uses the host monotonic clock, not simulation time; capture/analysis adds overhead. Core samples while advancing, retaining up to eight trailing frames plus boundary/return frames. Cancellation or closure interrupts advancement and attempts pause. Cancellation, deadline expiry, evidence-write failure or unknown pause state is not a successful wait. An external pause returns `wait_interrupted` without resuming again. A non-battle frame without terminal proof still returns `outcome_unknown` and `session_input_blocked=true`, even if paused battle is subsequently recognized. Blocked device I/O or process failure can still prevent pause.

`terminal` distinguishes `victory`, `defeat`, `unknown` and `still_in_battle`, with recognition source, raw match confidence and `terminal_evidence_path`. Resources currently cover explicit CN/English success/failure titles only, not verified support for every language or screen. Total kills, a black screen or leaving battle never imply victory; successful waiting is not battle success. Once a non-battle frame is observed, the session rejects further input and never toggles pause on menus. Exit evidence is exempt from the ordinary 128-image rotation and needs manual cleanup. User testimony must remain separate from image evidence.

The split-title fallback accepts exact `MISSION` above `ACCOMPLISHED` only after an actionable battle frame was observed, with battle UI still recognized and no running button visible. Both words must occur in the same frame, each score at least 0.9, with similar heights, left alignment and adjacent lines; fragments are never accumulated across captures. `terminal.split_victory` retains fragment rectangles/scores and acceptance or rejection reasons; accepted evidence uses `BattleControlVictorySplit` and the lower score. Complete and split success titles agree rather than conflict, but any confirmed defeat keeps the verdict unknown and input quarantined. `MISSION` alone or generic `MISSION RESULTS`/行动结束 is not victory evidence. Synthetic regressions do not establish recognition of real animated titles or independent result pages.

For complete static queries, explicitly attach with `battle_attach(stage_name="act55side_ex01", mode="normal")`, then call `intel_query(kind="stage", identifier="act55side_ex01", complete_stage=true, session_id="…")`. It reuses the configured provider, bypasses the page cache and bounds collection to 100 requests/120 seconds, checking region, stage, mode, cross-page provenance and duplicate IDs. The structured contract requires `game_id`, `server`, `mode`, `provenance`, pagination metadata `routes_page`/`spawns_page` with `page`, `has_more` and optional `total`, and top-level `routes`/`spawns` arrays or pagination `items`. Routes use `route_id`; spawns use `spawn_id`, `enemy_game_id`, `route_id`, `count`. Unknown provider schemas are not guessed.

For `coordinate_system="public_row_col"`, conversion uses the attached height: `x=col, y=height-1-row`. Points gain `maa_location`; tile matrices reverse row order. Raw responses remain in `pages`. `maa_grid` is unchanged; other systems remain unknown. Missing fields/pages or provider partial data return `partial` with per-section `completeness`, never empty substitutes. Identity/version conflicts and provider failures raise errors. `expires_at` is Unix seconds; expiry is explicit, absence means unknown freshness, and the five-minute cache does not prove current game data. Real-provider acceptance is still required: mock success proves neither all five information categories nor dynamic summon counts.

Supply per-battle configuration with `--loadout-config /absolute/loadout.json`; it is read at startup and requires a restart after edits. This example is not an observed loadout:

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

Region, stage and explicit mode must match. Optional fields include `actual_level`, `potential`, `module_id`, `module_level`, `skill_level` and `mastery`; omitted values are unknown. Snapshot `decision_context` preserves field sources, user confirmation and the configuration hash separately, without overwriting native observations or treating user confirmation as visual verification. SP, active phases, duration and talents remain unknown; ambiguous game IDs never silently select the first candidate.

`battle_evaluate_placement(session_id, placements)` checks a candidate whole formation without device input. Each entry supplies `operator_game_id`, `location`, `direction` and optionally `assumed_elite`; otherwise elite comes from supplied configuration. Results include base cells, geometric allied coverage, static deployment compatibility and cell conflicts. Unknown elite/ranges never use profession defaults. This does not verify live occupancy, healing eligibility, survival or optimality, and excludes skill, module, talent and special terrain effects. Live enemy tracking remains unsupported.

## Scene evidence, preparation and restricted operations

`scene` distinguishes `running / paused / transition / terminal / unknown`. Every sampled frame checks terminal evidence, which takes precedence over residual battle UI. After recognized battle, an in-battle `transition` blocks input with `input_block_reason=pause_verification_pending`, not permanent terminal quarantine. Recovery requires two consecutive fresh captures with positive pause evidence; a running or transition frame resets the count. Missing running buttons alone never prove pause. Pause cleanup budgets read-only verification for the greater of 3000 ms or twice the largest observed frame capture-and-analysis time plus 1000 ms, allowing for two confirmation frames and polling. The remaining command deadline always caps this budget; cancellation stops verification. Persistent uncertainty keeps input blocked.

`battle_observe`, `battle_capture_evidence`, and `battle_collect_terminal(request)` can collect recovery evidence without input. `battle_collect_terminal.duration_ms` is 100–8000 and cannot exceed `timeout_ms`; `status=succeeded` means collection completed, not pause recovery or battle victory. Inspect `pause_verified`, `session_input_blocked`, and `terminal`. A mutating command admitted while blocked never executes, even if its observation completes recovery: inspect fresh evidence and issue a new operation, explicitly adopting a manual pause if needed. No old actions are replayed or historical results rewritten. Unknown/non-battle frames after battle, terminal/conflicting evidence, and speed-readback failures retain `input_block_reason=terminal_or_safety_quarantine`; positive pause evidence cannot unlock these. `session_terminal` retains verdicts and evidence across black frames; conflicting verdicts remain unknown. Additional Core commands are `set_speed`, `finalize`, `prepare`, `bind`; preparation initializes with `preparation_only=true` and binding reuses that controller.

Pause recognition first requires an exact `PAUSE` match. If it does not reach 0.9, the CN label `暂停中` is checked within `[560,350,160,70]`. Chinese recognition accepts either one complete box or two to three spatially ordered, same-row fragments from one OCR call, each scoring at least 0.9. Only bounded gaps and overlaps are allowed; missing characters, extra prefixes/suffixes, different rows, excessive overlap, and boxes outside the ROI are rejected. Fragments are never joined across frames. Complete labels use source `BattleControlPausedLocalized`; split labels use `BattleControlPausedLocalizedSplit`. `pause_evidence` retains `fragments`, complete text, the minimum fragment `score`, union rectangle, frame ID/time, and `accepted`/`rejection_reason`. `PAUSEX` is not substitute evidence. Running-button and terminal detection still take precedence; recovery still requires two consecutive frames and never adopts pause ownership automatically.

When pause recognition fails, `pause_diagnostics` preserves the same-frame ID/time, configured ROI, required score, preprocessing settings, and nullable match. `localized.candidates` and the formal Chinese rule share one OCR call without the required-text filter, with `candidate_source=same_frame_localized_pause_rule_ocr`. `localized.fragment_rule` records acceptance or the rejection reason; only an accepted rule yields pause evidence, never an individual candidate by itself. English diagnostic candidates still come from a separate unfiltered call and do not replace formal matching. A null match is not a measured low score. Native geometry regressions cover the reported overlapping boxes, and synthetic text regressions use the production OCR path. Neither validates game fonts, effect occlusion, or other servers. Real post-deployment OCR, 1x/2x, and full-battle acceptance still require local device validation; the speed threshold is unchanged.

`skill_ready` is true or null; classifier negatives no longer claim confirmed cooldown. `skill_evidence` preserves the same-frame ID, time, ROI, base point, class score and probabilities. `probabilities` is ordered `c, n, y`; `score` belongs to the winning class, not necessarily the ready class `y`. Only a winning `y` with a score of at least 0.9 yields true. Active phase, ammo and cooldown can remain unknown; scores are not calibrated accuracy. Preserve `Battle frame` / `Battle command result` entries in `asst.log`, PNGs in `battle-control/<session>/`, and `operations.sqlite3`. Synthetic regression does not establish real OCR, ROI or button compatibility; local validation remains necessary, but local screenshots are not required inputs for cloud development.

After connecting and before attach, `battle_observe_preparation(stage_name, mode)` reads the manually opened formation page without device input. It returns names, candidate IDs, name rectangles and evidence; selected skills, progression and support identity remain unknown, with no detail navigation. Bind the latest snapshot using `battle_attach(..., preparation_id=..., confirm_preparation_unchanged=true)`. This is caller reconfirmation, not continuous visual verification. After roster/skill/stage/account changes, call `battle_invalidate_preparation` and recapture. Recapture, binding consumption or close prevents old snapshot reuse; live identity discrepancies appear as conflicts in `decision_context.preparation`.

Repeat startup option `--allow-summon GAME_ID` only for cards confirmed to use ordinary drag deployment. Use `battle_step(action="deploy")` with the usual arguments plus the observed `card_id`; it must identify a unique, available, unchanged card no older than 30 seconds. Each attempt creates `instance:<operation_id>` without overwriting same-name instances. Position/lifecycle are unverified action history; uncertain disappearance never automatically frees a tile. Instance skills/retreat, ambiguous cards and unapproved devices remain rejected; a helper return does not prove deployment.

`battle_set_speed(request)` accepts `session_id`, `operation_id`, `multiplier=1|2` and `timeout_ms`. Only while positively paused, it reads speed, switches once if needed, and reads back with before/after evidence. It preserves pause ownership and never resumes; repeated 2x does not toggle to 1x. Unknown speed causes no click; unverifiable post-change state locks input. The host alone decides whether to accelerate. Wait remains host time: Core reserves cleanup based on observed capture cost and refuses resume if insufficient. `cleanup_reserve_ms`, `resumed_interval_upper_bound_ms`, `deadline_margin_ms` are diagnostics, not exact simulation-time guarantees.

1X prefers OCR scoring at least 0.9. Otherwise, `BattleControlSpeedOneTemplate` reuses the existing `BattleSpeedUp.png` in the same speed ROI, also requiring at least 0.9. `one_x_ocr_evidence` and `one_x_template_evidence` retain the individual results; `one_x_evidence` is the selected result. 2X still uses `BattleSpeedUpCheck`. Conflicting 1X/2X evidence remains unknown; missing 2X evidence never implies 1X. Template regressions do not validate the report's game screenshot.

## Pinned raw stage snapshots

`--intel-config` supports `provider="raw"` for trusted local files without automatic downloads. Configure `root` (absolute directory), `repository`, `commit` (40 hex characters), `data_version`, `region`, `stages`, and `enemy_database`. `stages` maps stage IDs to `{"path":"relative/path","sha256":"64-character hash"}`; `enemy_database` uses the same file structure. Each file is capped at 64 MiB, SHA-256 verified and confined to the root.

Use the attached session's `intel_query(..., complete_stage=true)`. The adapter separately handles top-down map rows and bottom-up routes, emits `maa_grid`, and does not treat wait checkpoints as movement. One SPAWN action produces one record, retaining count, delays, interval, hidden groups and blocking fields. Enemy level inheritance, definition flags and level overrides support both Key/Value and key/value. Unknown mechanics and non-SPAWN actions are retained, never replaced with empty collections. `mode_modifiers` selects difficulty masks without claiming rune interpretation; `alignment=unknown`. `mode=normal|challenge` denotes difficulty, not practice/resource-consuming entry. External providers receive mode only if advertised in their tool schema, and caches isolate modes. Hashes and declared versions prove neither installed-game compatibility nor independent provider interoperability. Game data is not redistributed with the server.

## Core protocol

Initialize with `AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})`, call `AsstStart`, and await `SubTaskExtraInfo` with `what="BattleControlReady"` (protocol version 1). Submit `session_id`, `command_id`, `action`, `args`, and `timeout_ms` through `AsstSetTaskParams`; await `what="BattleControlResult"`. Only one command is accepted at a time; wait for its callback before submitting another.

Actions are `observe`, `capture`, `map`, `identify`, `deploy`, `retreat`, `skill`, `set_paused`, and `step`. `step` accepts one preflighted tactical action, captures immediately afterward, requests a pause, then verifies and returns a fresh paused frame; it cannot guarantee a pause if device I/O or the MCP process itself fails. Parameter updates only enqueue work; device access stays on the Core task thread. Native clients must supply their own external watchdog and deduplication: the C ABI alone cannot guarantee a blocking I/O deadline. The MCP server supplies that process-level watchdog. Experiments and optimization are future work.

`identify` accepts the optional strict boolean `args.adopt_manual_pause` (default false). Explicit adoption claims the recognized pause without toggling it; it does not authorize resume.

`step` also accepts `args.action="wait"` and `args.duration_ms` without unit arguments. During execution, `AsstSetTaskParams` accepts `{"session_id":"…","cancel_command_id":"…"}`. This only signals cancellation for that command, performs no device input and creates no second result. Late cancellation cannot affect the next command. The original callback returns cleanup results, and MCP persists/deduplicates using the original `operation_id`.
