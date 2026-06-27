"""Tests for ticker-mcp server — ToolRegistry + all tools."""

import json
import os
import sys
import tempfile

import tempfile

# Point to the server module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use temp file for A2A data during tests
os.environ["_A2A_DATA_FILE"] = os.path.join(
    os.path.dirname(__file__), ".test_a2a_data.json"
)

import server
from server import registry


# ─── ToolRegistry ────────────────────────────────────────

def test_registry_list_tools():
    """Registry should list all registered tools."""
    tools = registry.list()
    assert len(tools) >= 8, f"Expected >=8 tools, got {len(tools)}"
    names = [t.name for t in tools]
    assert "mcp_validate_config" in names
    assert "a2a_register" in names
    assert "mcp_inspect_schema" in names


def test_registry_tool_has_schema():
    """Every registered tool should have a valid inputSchema."""
    for tool in registry.list():
        assert "type" in tool.inputSchema
        assert tool.inputSchema["type"] == "object"
        assert "properties" in tool.inputSchema


# ─── Meta-MCP Tools ──────────────────────────────────────

def test_validate_config_valid():
    result = server.mcp_validate_config(
        '{"mcpServers": {"demo": {"command": "python", "args": ["s.py"]}}}'
    )
    assert result["valid"] is True
    assert result["server_count"] == 1


def test_validate_config_missing_command():
    result = server.mcp_validate_config('{"test": {}}')
    assert result["valid"] is False
    assert len(result["issues"]) > 0


def test_validate_config_invalid_json():
    result = server.mcp_validate_config("not json")
    assert result["valid"] is False


def test_inspect_schema():
    schema = json.dumps({
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The name"},
            "count": {"type": "integer"},
        },
        "required": ["name"],
    })
    result = server.mcp_inspect_schema(schema)
    assert result["required_count"] == 1
    assert result["optional_count"] == 1
    assert any(p["name"] == "name" for p in result["parameters"])


def test_inspect_schema_invalid_json():
    result = server.mcp_inspect_schema("{{{")
    assert "error" in result


def test_format_response():
    result = server.mcp_format_response('{"ok": true}', label="test")
    assert "content" in result
    assert "meta" in result


# ─── A2A Tools ───────────────────────────────────────────

def test_a2a_register():
    result = server.a2a_register("TestBot", "Test agent", ["test", "demo"])
    assert result["status"] == "registered"
    assert result["agent_id"] == "testbot"


def test_a2a_discover_by_capability():
    server.a2a_register("DiscoverBot", "Discovery test", ["search", "index"])
    result = server.a2a_discover(capability="search")
    assert result["result_count"] >= 1
    assert any(a["name"] == "DiscoverBot" for a in result["results"])


def test_a2a_discover_by_keyword():
    result = server.a2a_discover(keyword="DiscoverBot")
    assert result["result_count"] >= 1


def test_a2a_list_all():
    result = server.a2a_list_all()
    assert result["total_agents"] >= 1


def test_a2a_submit_task():
    result = server.a2a_submit_task(
        task_type="echo",
        input_data_json='{"hello": "world"}',
    )
    assert result["status"] in ("pending", "working", "completed")
    assert len(result["task_id"]) > 0


def test_a2a_get_task():
    submit = server.a2a_submit_task("echo", '"test"')
    task_id = submit["task_id"]
    result = server.a2a_get_task(task_id)
    assert result["task_id"] == task_id
    assert result["status"] in ("pending", "working", "completed")


def test_a2a_get_task_not_found():
    result = server.a2a_get_task("nonexistent")
    assert "error" in result


def test_a2a_list_tasks():
    result = server.a2a_list_tasks()
    assert result["total"] >= 1


def test_a2a_list_tasks_filtered():
    result = server.a2a_list_tasks(status_filter="completed")
    assert "tasks" in result


# ─── Edge Cases ──────────────────────────────────────────

def test_unknown_tool():
    import asyncio
    result = asyncio.run(registry.call("nonexistent_tool", {}))
    assert "Unknown tool" in result[0].text


def test_tool_error_handling():
    """Calling a tool with wrong params should not crash."""
    import asyncio
    # mcp_validate_config expects a string, pass int instead
    result = asyncio.run(registry.call("mcp_validate_config", {}))
    assert result[0].text is not None


# ─── Cleanup ─────────────────────────────────────────────

def teardown_module():
    """Clean up test data file."""
    data_file = os.environ.get("_A2A_DATA_FILE", "")
    if data_file and os.path.isfile(data_file):
        os.remove(data_file)