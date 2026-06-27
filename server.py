"""
ticker-mcp — Meta-MCP & A2A server.

A well-architected MCP server that helps build, test, and manage
MCP itself, plus Agent-to-Agent (A2A) capability registry.

Layers:
  Framework    — ToolRegistry with auto schema inference
  Meta-MCP     — Tools for MCP: validate, inspect, generate, format
  A2A          — Agent capability registry & task delegation
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Union

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    import mcp.server.models as models
except ImportError:
    raise ImportError("MCP SDK required: pip install 'mcp[cli]'")

logger = logging.getLogger("ticker-mcp")

# ══════════════════════════════════════════════════════════
#  FRAMEWORK — ToolRegistry
# ══════════════════════════════════════════════════════════

class ToolRegistry:
    """Decorator-based tool registry with auto schema inference.
    
    Usage:
        registry = ToolRegistry()
        
        @registry.tool("name", "description")
        def my_tool(param1: str, param2: int = 42) -> dict: ...
    """
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._handlers: dict[str, Callable] = {}
    
    def tool(self, name: str, description: str = ""):
        def decorator(func: Callable):
            self._tools[name] = Tool(
                name=name,
                description=description or (func.__doc__ or "").strip(),
                inputSchema=self._infer_schema(func),
            )
            self._handlers[name] = func
            return func
        return decorator
    
    def list(self) -> list[Tool]:
        return list(self._tools.values())
    
    async def call(self, name: str, arguments: dict) -> list[TextContent]:
        if name not in self._handlers:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = self._handlers[name](**arguments)
            if isinstance(result, (dict, list)):
                text = json.dumps(result, ensure_ascii=False, indent=2)
            else:
                text = str(result)
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]
    
    @staticmethod
    def _infer_schema(func: Callable) -> dict:
        import inspect
        sig = inspect.signature(func)
        props, required = {}, []
        type_map = {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
            list: {"type": "array", "items": {}},
            dict: {"type": "object"},
        }
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            hint = param.annotation if param.annotation != inspect.Parameter.empty else str
            origin = getattr(hint, "__origin__", None) if hasattr(hint, "__origin__") else None
            if origin is Union and type(None) in hint.__args__:
                actual = [t for t in hint.__args__ if t is not type(None)][0]
                entry = type_map.get(actual, {"type": "string"}).copy()
                entry["type"] = [entry["type"], "null"]
            else:
                entry = type_map.get(hint, {"type": "string"}).copy()
            entry["description"] = f"Parameter {pname}"
            if param.default == inspect.Parameter.empty:
                required.append(pname)
            else:
                entry["default"] = (
                    param.default if not isinstance(param.default, Enum)
                    else param.default.value
                )
            props[pname] = entry
        return {"type": "object", "properties": props, "required": required}


registry = ToolRegistry()

# ══════════════════════════════════════════════════════════
#  META-MCP — Tools about MCP itself
# ══════════════════════════════════════════════════════════

@registry.tool("mcp_validate_config",
    "Validate an MCP server configuration (Claude Desktop / Cursor mcpServers JSON). "
    "Checks for common errors: missing required fields, invalid transport, bad paths."
)
def mcp_validate_config(config_json: str) -> dict:
    """Parse and validate an MCP server configuration block.
    
    Args:
        config_json: JSON string of an mcpServers config (or a single server entry)
    """
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"Invalid JSON: {e}"]}
    
    issues = []
    warnings = []
    
    if isinstance(config, dict):
        if "mcpServers" in config:
            servers = config["mcpServers"]
        else:
            servers = config
        
        if isinstance(servers, dict):
            for name, srv in servers.items():
                if not isinstance(srv, dict):
                    issues.append(f"'{name}': server entry must be an object")
                    continue
                if "command" not in srv and "url" not in srv:
                    issues.append(f"'{name}': missing 'command' (stdio) or 'url' (HTTP)")
                if "command" in srv and not isinstance(srv["command"], str):
                    issues.append(f"'{name}': 'command' must be a string")
                if "args" in srv and not isinstance(srv["args"], list):
                    warnings.append(f"'{name}': 'args' should be a list")
    
    return {
        "valid": len(issues) == 0,
        "server_count": len(servers) if isinstance(servers, dict) else 0,
        "issues": issues,
        "warnings": warnings,
    }


@registry.tool("mcp_inspect_schema",
    "Analyze a JSON Schema and describe what MCP tool it defines. "
    "Shows parameter names, types, required fields, and generates example usage."
)
def mcp_inspect_schema(schema_json: str) -> dict:
    """Inspect a JSON Schema and extract MCP-relevant information.
    
    Args:
        schema_json: JSON Schema string (draft-07 or similar)
    """
    try:
        schema = json.loads(schema_json)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
    
    props = schema.get("properties", {})
    required = schema.get("required", [])
    
    params = []
    for name, prop in props.items():
        param = {
            "name": name,
            "type": prop.get("type", "unknown"),
            "required": name in required,
            "description": prop.get("description", ""),
        }
        if "default" in prop:
            param["default"] = prop["default"]
        if "enum" in prop:
            param["enum"] = prop["enum"]
        params.append(param)
    
    example = {}
    for p in params:
        if p["required"]:
            example[p["name"]] = _example_value(p["type"])
    
    return {
        "type": schema.get("type", "object"),
        "parameters": params,
        "required_count": len(required),
        "optional_count": len(props) - len(required),
        "example_call": example,
    }


def _example_value(t: str) -> Any:
    mapping = {
        "string": "example",
        "integer": 0,
        "number": 0.0,
        "boolean": True,
        "array": [],
        "object": {},
    }
    return mapping.get(t, "example")


@registry.tool("mcp_format_response",
    "Format data as a proper MCP response structure. "
    "Supports TextContent, and structured data responses."
)
def mcp_format_response(
    data_json: str,
    response_type: str = "text",
    label: str = "",
) -> dict:
    """Wrap data into MCP response format.
    
    Args:
        data_json: The data to wrap
        response_type: 'text' for TextContent
        label: Optional label for the response
    """
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        data = data_json
    
    return {
        "content": [
            {
                "type": response_type,
                "text": json.dumps(data, ensure_ascii=False, indent=2)
                if isinstance(data, (dict, list)) else str(data),
            }
        ],
        "meta": {
            "format": "mcp-response",
            "label": label or "unlabeled",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# ══════════════════════════════════════════════════════════
#  A2A — Agent-to-Agent capability registry & task delegation
# ══════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    PENDING = "pending"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"


# File-backed A2A store (persists across restarts)

_A2A_DATA_FILE = os.environ.get(
    "_A2A_DATA_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "a2a_data.json"),
)


class _FileStore:
    """Thread-safe JSON file-backed key-value store with atomic writes."""
    
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()
    
    def _load(self):
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("Failed to load A2A data file, starting fresh: %s", e)
                self._data = {}
        else:
            self._data = {"agents": {}, "tasks": {}}
        self._data.setdefault("agents", {})
        self._data.setdefault("tasks", {})
    
    def _save(self):
        """Atomic write: write to temp file, then rename into place."""
        tmp_path = self._path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._path)
        except (IOError, OSError) as e:
            logger.error("A2A data persistence failed: %s", e)
            raise RuntimeError("Data persistence failed") from e
    
    @property
    def agents(self) -> dict:
        return self._data["agents"]
    
    @property
    def tasks(self) -> dict:
        return self._data["tasks"]
    
    def set_agent(self, agent_id: str, data: dict):
        with self._lock:
            self._data["agents"][agent_id] = data
            self._save()
    
    def set_task(self, task_id: str, data: dict):
        with self._lock:
            self._data["tasks"][task_id] = data
            self._save()


_a2a_store = _FileStore(_A2A_DATA_FILE)


@registry.tool("a2a_register",
    "Register an agent capability in the A2A registry. "
    "Share what your agent can do so other agents can discover and delegate to you."
)
def a2a_register(
    agent_name: str,
    description: str,
    capabilities: list[str],
    contact_endpoint: str = "internal",
) -> dict:
    """Register or update an agent in the A2A capability registry.
    
    Args:
        agent_name: Unique name for this agent
        description: What this agent does
        capabilities: List of capability strings, e.g. ['data_analysis', 'code_review']
        contact_endpoint: How to reach this agent (URL or 'internal')
    """
    # Input validation
    if not agent_name or not isinstance(agent_name, str):
        raise ValueError("agent_name must be a non-empty string")
    if not isinstance(description, str):
        raise ValueError("description must be a string")
    if not capabilities or not isinstance(capabilities, list):
        raise ValueError("capabilities must be a non-empty list")
    if not all(isinstance(c, str) for c in capabilities):
        raise ValueError("each capability must be a string")
    if not isinstance(contact_endpoint, str):
        raise ValueError("contact_endpoint must be a string")
    
    agent_id = agent_name.lower().replace(" ", "-")
    _a2a_store.set_agent(agent_id, {
        "agent_id": agent_id,
        "name": agent_name,
        "description": description,
        "capabilities": capabilities,
        "contact_endpoint": contact_endpoint,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "status": "registered",
        "agent_id": agent_id,
        "capability_count": len(capabilities),
        "message": f"Agent '{agent_name}' registered with {len(capabilities)} capabilities",
    }


@registry.tool("a2a_discover",
    "Discover agents by capability. Search the A2A registry for agents "
    "that can perform specific tasks."
)
def a2a_discover(
    capability: str = "",
    keyword: str = "",
) -> dict:
    """Find agents matching a capability or keyword.
    
    Args:
        capability: Filter by capability name (partial match)
        keyword: Filter by keyword in name or description
    """
    results = []
    for aid, agent in _a2a_store.agents.items():
        score = 0
        if capability:
            for cap in agent["capabilities"]:
                if capability.lower() in cap.lower():
                    score += 1
        if keyword:
            if keyword.lower() in agent["name"].lower():
                score += 1
            if keyword.lower() in agent["description"].lower():
                score += 1
        
        if score > 0 or (not capability and not keyword):
            results.append({
                "agent_id": agent["agent_id"],
                "name": agent["name"],
                "description": agent["description"],
                "capabilities": agent["capabilities"],
                "match_score": score,
            })
    
    results.sort(key=lambda x: x["match_score"], reverse=True)
    
    return {
        "total_agents": len(_a2a_store.agents),
        "query": {"capability": capability, "keyword": keyword},
        "results": results,
        "result_count": len(results),
    }


@registry.tool("a2a_list_all",
    "List all registered agents and their capabilities."
)
def a2a_list_all() -> dict:
    """List every agent currently registered in the A2A registry."""
    agents = []
    for aid, agent in _a2a_store.agents.items():
        agents.append({
            "agent_id": agent["agent_id"],
            "name": agent["name"],
            "capabilities": agent["capabilities"],
            "description": agent["description"][:80],
        })
    return {
        "total_agents": len(agents),
        "agents": agents,
    }


@registry.tool("a2a_submit_task",
    "Submit a task to be processed by an agent (or for general processing). "
    "Returns a task_id for status polling."
)
def a2a_submit_task(
    task_type: str,
    input_data_json: str,
    target_agent: str = "any",
) -> dict:
    """Submit a new task to the A2A task system.
    
    Args:
        task_type: Type/category of task, e.g. 'code_review', 'data_analysis'
        input_data_json: JSON string with task input data
        target_agent: Specific agent to handle this, or 'any' for auto-assign
    """
    task_id = str(uuid.uuid4())[:8]
    
    try:
        input_data = json.loads(input_data_json)
    except json.JSONDecodeError:
        input_data = {"raw": input_data_json}
    
    task = {
        "task_id": task_id,
        "task_type": task_type,
        "status": TaskStatus.PENDING.value,
        "input": input_data,
        "target_agent": target_agent,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output": None,
        "error": None,
    }
    _a2a_store.set_task(task_id, task)
    
    _auto_process_task(task_id)
    
    return {
        "task_id": task_id,
        "status": task["status"],
        "message": f"Task {task_id} submitted",
    }


@registry.tool("a2a_get_task",
    "Get the status and result of a previously submitted task."
)
def a2a_get_task(task_id: str) -> dict:
    """Check task status and retrieve results.
    
    Args:
        task_id: The task ID returned by a2a_submit_task
    """
    if task_id not in _a2a_store.tasks:
        return {"error": f"Task {task_id} not found"}
    
    task = _a2a_store.tasks[task_id]
    return {
        "task_id": task["task_id"],
        "task_type": task["task_type"],
        "status": task["status"],
        "created_at": task["created_at"],
        "output": task.get("output"),
        "error": task.get("error"),
    }


@registry.tool("a2a_list_tasks",
    "List all tasks and their current status."
)
def a2a_list_tasks(status_filter: str = "") -> dict:
    """List all tasks, optionally filtered by status.
    
    Args:
        status_filter: Optional filter: 'pending', 'working', 'completed', 'failed'
    """
    tasks = []
    for tid, task in _a2a_store.tasks.items():
        if status_filter and task["status"] != status_filter:
            continue
        tasks.append({
            "task_id": task["task_id"],
            "task_type": task["task_type"],
            "status": task["status"],
            "created_at": task["created_at"],
        })
    
    tasks.sort(key=lambda x: x["created_at"], reverse=True)
    
    return {
        "total": len(tasks),
        "filter": status_filter or "all",
        "tasks": tasks,
    }


def _auto_process_task(task_id: str):
    """Auto-process simple tasks for demo purposes.
    
    In production, this would dispatch to real agent handlers.
    """
    task = _a2a_store.tasks.get(task_id)
    if not task:
        return
    
    task["status"] = TaskStatus.WORKING.value
    
    ttype = task["task_type"]
    inp = task["input"]
    
    try:
        if ttype == "echo":
            output = {"echo": inp}
        elif ttype == "analyze_json":
            if isinstance(inp, dict):
                output = {
                    "keys": list(inp.keys()),
                    "value_types": {k: type(v).__name__ for k, v in inp.items()},
                    "depth": _depth(inp),
                }
            else:
                output = {"note": "Input is not a JSON object", "type": type(inp).__name__}
        elif ttype == "hash":
            raw = json.dumps(inp) if isinstance(inp, dict) else str(inp)
            output = {
                "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "length": len(raw),
            }
        else:
            output = {
                "note": f"Task type '{ttype}' processed",
                "input_summary": str(inp)[:200],
            }
        
        task["status"] = TaskStatus.COMPLETED.value
        task["output"] = output
    except Exception as e:
        task["status"] = TaskStatus.FAILED.value
        task["error"] = str(e)


def _depth(obj: Any, max_d: int = 10) -> int:
    """Compute nesting depth of a dict."""
    if not isinstance(obj, dict) or max_d <= 0:
        return 0
    return 1 + max((_depth(v, max_d - 1) for v in obj.values()), default=0)


# ══════════════════════════════════════════════════════════
#  SERVER — MCP protocol binding
# ══════════════════════════════════════════════════════════

server = Server("ticker-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return registry.list()


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return await registry.call(name, arguments)


# ══════════════════════════════════════════════════════════
#  SSE TRANSPORT — with auth middleware
# ══════════════════════════════════════════════════════════

def _make_sse_app(token: str):
    """Create Starlette app with optional token auth middleware."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.requests import Request
    from starlette.routing import Route
    
    sse = SseServerTransport("/messages/")
    
    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await server.run(
                streams[0], streams[1],
                models.InitializationOptions(
                    server_name="ticker-mcp",
                    server_version="0.3.0",
                ),
            )
    
    if token:
        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                auth = request.headers.get("Authorization", "")
                query_token = request.query_params.get("token", "")
                expected = f"Bearer {token}"
                if auth == expected or query_token == token:
                    return await call_next(request)
                return JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                )
        
        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
        ])
        app.add_middleware(AuthMiddleware)
    else:
        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
        ])
    
    return app


def main():
    import asyncio
    import argparse
    
    token = os.environ.get("TICKER_MCP_TOKEN", "")
    
    parser = argparse.ArgumentParser(description="ticker-mcp: Meta-MCP & A2A server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                       help="Transport protocol (default: stdio)")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                       help="Bind address for SSE (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080,
                       help="Port for SSE transport (default: 8080)")
    parser.add_argument("--token", type=str, default=token,
                       help="Auth token for SSE (env: TICKER_MCP_TOKEN)")
    args = parser.parse_args()
    
    print(f"ticker-mcp ({args.transport})")
    print(f"Tools: {len(registry.list())}")
    for t in registry.list():
        print(f"  {t.name:25s} {t.description[:55]}")
    
    if args.transport == "sse":
        # Security warning when binding to all interfaces without auth
        if args.host == "0.0.0.0" and not args.token:
            print("\n" + "!" * 60)
            print("  SECURITY WARNING: Server bound to 0.0.0.0 without auth token.")
            print("  Anyone on the network can access this MCP server.")
            print("  Set --token <key> or TICKER_MCP_TOKEN environment variable.")
            print("!" * 60 + "\n")
        elif args.host != "127.0.0.1" and not args.token:
            print(f"\n  ⚠️  Bound to {args.host} without auth token. Use --token for security.\n")
        
        _run_sse(args.host, args.port, args.token)
    else:
        asyncio.run(_run_stdio())


def _run_sse(host: str, port: int, token: str):
    import uvicorn
    app = _make_sse_app(token)
    print(f"Listening on http://{host}:{port}/sse")
    uvicorn.run(app, host=host, port=port)


async def _run_stdio():
    async with stdio_server() as streams:
        await server.run(
            streams[0], streams[1],
            models.InitializationOptions(
                server_name="ticker-mcp",
                server_version="0.3.0",
            ),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
