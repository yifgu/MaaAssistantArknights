from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

server = FastMCP("test-intelligence")


@server.tool()
def get_stage(
    server: str,
    game_id: str,
    include_map: bool,
    include_routes: bool,
    include_spawns: bool,
    routes_page: dict,
    spawns_page: dict,
) -> CallToolResult:
    assert routes_page == {"page": 2, "page_size": 50}
    assert spawns_page == {"page": 1, "page_size": 50}
    assert include_map and include_routes and include_spawns
    if game_id == "failure":
        return CallToolResult(
            content=[], structuredContent={"status": "database_unavailable"}
        )
    if game_id == "unstructured":
        return CallToolResult(
            content=[TextContent(type="text", text="unstructured facts")]
        )
    data = {
        "status": "ok",
        "server": server,
        "game_id": game_id,
        "provenance": {"build_id": "test-snapshot"},
        "routes_page": {"has_more": True},
    }
    return CallToolResult(content=[], structuredContent=data)


if __name__ == "__main__":
    server.run()
