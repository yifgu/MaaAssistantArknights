# Maa Battle MCP

Local stdio MCP server backed by this checkout's persistent `BattleControl` task.
Default `--tool-profile minimal` exposes ten tools; `--tool-profile full` retains
the 27 non-intelligence compatibility/media/geometry tools. Configured intelligence
adds `intel_query` to either profile; without configuration it is not registered.

Use `battle_observe` for passive structured state, opt-in `include_image` /
`include_map`, or bounded read-only `duration_ms` collection while input is blocked.
It never implicitly identifies or sends input. Minimal responses contain one
top-level `battle_decision` and operation/safety/evidence metadata, without default
image attachments; full retains legacy response shapes. Original PNGs are exposed
through operation-bound `battle-evidence://{session_id}/{operation_id}/{observation_id}`
resources (`image/png`), not arbitrary paths or fresh captures.

`battle_get_operation(..., detail="full")` reads the unchanged persisted result
and complete boundary/return structured snapshots plus evidence references,
without new observation, input, journal writes or image attachments. The default
`detail="summary"` follows the profile; full remains unabridged for compatibility.
Full-profile pause/speed steps inline their native `image_path` PNG, deduplicating
identical image paths.

Preparation fallback fields advertise their recorded PNG `retrieval.resource_uri`.
Minimal omits full-only tool hints; full preserves the compatible tool. Historical
reads apply the same retrieval-metadata projection without rewriting journal data.

`battle_step.request` is an action-discriminated schema for deployment, retreat,
skill, wait, pause-only, and verified-paused 1x/2x speed changes. Each branch
advertises its required and forbidden fields; manual-pause adoption stays explicit.
Use Core, resources and Python from the same commit.

Build and run instructions, protocol details, limitations and local checks:

- [简体中文](../../docs/zh-cn/develop/mcp-server.md)
- [繁體中文](../../docs/zh-tw/develop/mcp-server.md)
- [English](../../docs/en-us/develop/mcp-server.md)
- [日本語](../../docs/ja-jp/develop/mcp-server.md)
- [한국어](../../docs/ko-kr/develop/mcp-server.md)
