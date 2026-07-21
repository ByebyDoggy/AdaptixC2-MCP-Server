"""
tools/agents.py — Agent management tools.

Tools for listing, inspecting, tagging, and removing agents,
and for querying teamserver listeners.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from tools._context  import ToolContext
from client.adaptix_client import AdaptixAPIError
from utils.validation import validate_agent_id, validate_agent_exists, validate_nonempty
from utils.logging   import get_logger

log = get_logger("tools.agents")


def register_agent_tools(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register agent management MCP tools."""

    @mcp.tool(description=(
        "List all active agents connected to the AdaptixC2 teamserver.\n"
        "Returns: OS, hostname, username, IP, process, sleep interval, elevation status."
    ))
    async def list_agents() -> str:
        agents = await ctx.agent_svc.list_agents()
        if not agents:
            return "No agents currently connected."
        lines = [f"Found {len(agents)} agent(s):\n"]
        for a in agents:
            lines.append(a.summary())
        return "\n".join(lines)

    @mcp.tool(description=(
        "Get detailed information about a specific agent by ID.\n"
        "Accepts both internal a_id and GUI-visible a_crc (8-char hex)."
    ))
    async def agent_info(agent_id: str) -> str:
        agent_id = validate_agent_id(agent_id)
        agent = await ctx.agent_svc.get_agent(agent_id)
        if agent is None:
            return f"Agent '{agent_id}' not found."
        return (
            f"Agent Details:\n"
            f"  ID:          {agent.id}\n"
            f"  Computer:    {agent.computer}\n"
            f"  Domain:      {agent.domain}\n"
            f"  Username:    {agent.username}\n"
            f"  Impersonated:{agent.impersonated}\n"
            f"  OS:          {agent.os_name} ({agent.os_desc})\n"
            f"  Arch:        {agent.arch}\n"
            f"  Process:     {agent.process} PID={agent.pid} TID={agent.tid}\n"
            f"  Elevated:    {agent.elevated}\n"
            f"  Internal IP: {agent.internal_ip}\n"
            f"  External IP: {agent.external_ip}\n"
            f"  Listener:    {agent.listener}\n"
            f"  Sleep:       {agent.sleep}s (jitter {agent.jitter}%)\n"
            f"  Tags:        {agent.tags or 'none'}\n"
        )

    @mcp.tool(description=(
        "Remove an agent session from the teamserver.\n"
        "This removes the C2 record — it does NOT kill the implant process."
    ))
    async def kill_agent(agent_id: str) -> str:
        agent_id = validate_agent_id(agent_id)
        await validate_agent_exists(ctx.client, agent_id)
        await ctx.agent_svc.remove_agent(agent_id)
        log.info("tool.kill_agent", agent_id=agent_id)
        return f"Agent '{agent_id}' removed from the teamserver."

    @mcp.tool(description=(
        "Set a text tag on one or more agents for organisation.\n"
        "agent_ids: comma-separated list of agent IDs."
    ))
    async def tag_agent(agent_ids: str, tag: str) -> str:
        ids = [i.strip() for i in agent_ids.split(",") if i.strip()]
        if not ids:
            return "Error: provide at least one agent_id."
        await ctx.client.agent_set_tag(ids, tag)
        return f"Tag '{tag}' applied to agents: {', '.join(ids)}"

    @mcp.tool(description="List all active listeners on the teamserver.")
    async def list_listeners() -> str:
        listeners = await ctx.client.list_listeners_raw()
        if not listeners:
            return "No listeners currently active."
        lines = [f"Found {len(listeners)} listener(s):\n"]
        for l_ in listeners:
            name   = l_.get("l_name", "?")
            ltype  = l_.get("l_type", "?")
            proto  = l_.get("l_protocol", "?")
            addr   = l_.get("l_agent_addr", "?")
            status = l_.get("l_status", "?")
            lines.append(f"  [{proto}/{ltype}] {name}  @ {addr}  [{status}]")
        return "\n".join(lines)

    @mcp.tool(description=(
        "Show execution history for a specific agent.\n"
        "Returns recent commands, their timestamps, statuses and outputs.\n"
        "Use this to avoid repeating commands if results are recent and valid."
    ))
    async def list_task_history(agent_id: str, limit: int = 20) -> str:
        from models.task import Task
        import datetime

        agent_id = validate_agent_id(agent_id)
        tasks_raw = await ctx.client.list_tasks(agent_id, limit=limit)
        if not tasks_raw:
            return f"No task history found for agent '{agent_id}'."

        lines = [f"Recent task history for agent {agent_id} (last {len(tasks_raw)}):\n"]
        for t_raw in tasks_raw:
            try:
                t = Task.model_validate(t_raw)
                dt = datetime.datetime.fromtimestamp(t.start_time).strftime('%H:%M:%S')
                status = "SUCCESS" if t.completed and not t.is_error else ("ERROR" if t.is_error else "PENDING")
                
                # Truncate output preview for LLM context efficiency
                out_preview = t.output.strip().replace("\n", " ")
                if len(out_preview) > 120:
                    out_preview = out_preview[:120] + "..."
                
                lines.append(f"  [{dt}] [{status}] {t.command_line} -> {out_preview}")
            except Exception:
                continue

        return "\n".join(lines)

    # ── Listener management ─────────────────────────────────────────────────

    @mcp.tool(description=(
        "Start a new listener on the teamserver.\n"
        "Args:\n"
        "  name : STRING — Listener name (e.g. 'my-http').\n"
        "  config_type : STRING — Listener type (e.g. 'beacon_http', 'beacon_dns', 'beacon_smb', 'beacon_tcp').\n"
        "  config : STRING — JSON/YAML configuration string for the listener.\n"
        "Example: create_listener('my-http', 'beacon_http', '{\"port\":8080,\"host\":\"0.0.0.0\"}')"
    ))
    async def create_listener(name: str, config_type: str, config: str) -> str:
        name = validate_nonempty(name, "name")
        config_type = validate_nonempty(config_type, "config_type")
        config = validate_nonempty(config, "config")
        log.info("tool.create_listener", name=name, type=config_type)
        try:
            await ctx.client.start_listener(name, config_type, config)
            return f"Listener '{name}' ({config_type}) created successfully."
        except AdaptixAPIError as e:
            return f"Failed to create listener: {e}"

    @mcp.tool(description=(
        "Stop a running listener.\n"
        "Use list_listeners first to find the name and type.\n"
        "Args: name (STR), config_type (STR)."
    ))
    async def stop_listener(name: str, config_type: str) -> str:
        name = validate_nonempty(name, "name")
        config_type = validate_nonempty(config_type, "config_type")
        log.info("tool.stop_listener", name=name, type=config_type)
        try:
            await ctx.client.stop_listener(name, config_type)
            return f"Listener '{name}' stopped."
        except AdaptixAPIError as e:
            return f"Failed to stop listener: {e}"

    @mcp.tool(description=(
        "Pause a running listener temporarily.\n"
        "Args: name (STR), config_type (STR)."
    ))
    async def pause_listener(name: str, config_type: str) -> str:
        name = validate_nonempty(name, "name")
        config_type = validate_nonempty(config_type, "config_type")
        log.info("tool.pause_listener", name=name, type=config_type)
        try:
            await ctx.client.pause_listener(name, config_type)
            return f"Listener '{name}' paused."
        except AdaptixAPIError as e:
            return f"Failed to pause listener: {e}"

    @mcp.tool(description=(
        "Resume a paused listener.\n"
        "Args: name (STR), config_type (STR)."
    ))
    async def resume_listener(name: str, config_type: str) -> str:
        name = validate_nonempty(name, "name")
        config_type = validate_nonempty(config_type, "config_type")
        log.info("tool.resume_listener", name=name, type=config_type)
        try:
            await ctx.client.resume_listener(name, config_type)
            return f"Listener '{name}' resumed."
        except AdaptixAPIError as e:
            return f"Failed to resume listener: {e}"

    @mcp.tool(description=(
        "Edit an existing listener's configuration.\n"
        "Args: name (STR), config_type (STR), config (STR) — new config JSON/YAML.\n"
        "Example: edit_listener('my-http', 'beacon_http', '{\"port\":9090,\"host\":\"0.0.0.0\"}')"
    ))
    async def edit_listener(name: str, config_type: str, config: str) -> str:
        name = validate_nonempty(name, "name")
        config_type = validate_nonempty(config_type, "config_type")
        config = validate_nonempty(config, "config")
        log.info("tool.edit_listener", name=name, type=config_type)
        try:
            await ctx.client.edit_listener(name, config_type, config)
            return f"Listener '{name}' configuration updated."
        except AdaptixAPIError as e:
            return f"Failed to edit listener: {e}"

    # ── Agent generation ────────────────────────────────────────────────────

    @mcp.tool(description=(
        "Build an agent payload for deployment.\n"
        "Args:\n"
        "  listener_names : STR — Comma-separated listener names the agent should connect to.\n"
        "  agent_name : STR — 'beacon' (Windows C) or 'gopher' (cross-platform Go).\n"
        "  os : STR — Target OS.\n"
        "         beacon: 'windows' (only choice).\n"
        "         gopher: 'windows' | 'linux' | 'macos'.\n"
        "  arch : STR — CPU architecture.\n"
        "         beacon: 'x64' | 'x86'.\n"
        "         gopher: 'amd64' | 'arm64'.\n"
        "  format : STR — Output format (beacon only).\n"
        "         'Exe' | 'Service Exe' | 'DLL' | 'Shellcode'.\n"
        "  sleep : STR — Agent heartbeat interval, e.g. '60' (seconds).\n"
        "  jitter : INT — Jitter percentage (0-100).\n"
        "  iat_hiding : BOOL — (beacon) Enable IAT hiding for Defender evasion (default: false).\n"
        "  user_agent : STR — (beacon) Custom HTTP User-Agent header.\n"
        "  rotation_mode : STR — (beacon) Callback rotation: 'sequential' | 'random' (default: 'random').\n"
        "  extra_config : STR — (optional) Extra JSON keys, e.g. "
        "'{\"proxy_host\":\"10.0.0.1\",\"proxy_port\":8080}'.\n"
        "\n"
        "Agent types:\n"
        "  beacon  → Windows only (x86/x64, compiled C via MinGW)\n"
        "           Formats: Exe (.exe), Service Exe (svc_*.exe), DLL (.dll), Shellcode (.bin)\n"
        "  gopher  → Cross-platform Go (windows/linux/macos, amd64/arm64)\n"
        "\n"
        "Example: generate_agent(listener_names='C2HTTP', agent_name='beacon', os='windows', arch='x64', format='Exe', iat_hiding=True)\n"
        "Example: generate_agent(listener_names='GopherTCP', agent_name='gopher', os='linux', arch='amd64')\n"
        "\n"
        "Returns the agent filename and base64-encoded binary content."
    ))
    async def generate_agent(
        listener_names: str,
        agent_name: str,
        os: str = "",
        arch: str = "",
        format: str = "",
        sleep: str = "",
        jitter: int = 0,
        iat_hiding: bool = False,
        user_agent: str = "",
        rotation_mode: str = "",
        extra_config: str = "",
    ) -> str:
        import base64, json

        # Parse listener list
        listeners = [l.strip() for l in listener_names.split(",") if l.strip()]
        if not listeners:
            return "Error: provide at least one listener name."
        agent_name = validate_nonempty(agent_name, "agent_name").lower()

        # Agent-type-specific defaults and validation
        if agent_name == "beacon":
            if not os: os = "windows"
            if not arch: arch = "x64"
            if not format: format = "Exe"
            if not sleep: sleep = "60"
            if not rotation_mode: rotation_mode = "random"
            if os != "windows":
                return "Error: beacon agent supports only 'windows' OS."
            if arch not in ("x86", "x64"):
                return "Error: beacon arch must be 'x86' or 'x64'."
            if format not in ("Exe", "Service Exe", "DLL", "Shellcode"):
                return "Error: beacon format must be 'Exe', 'Service Exe', 'DLL', or 'Shellcode'."
        elif agent_name == "gopher":
            if not os: os = "linux"
            if not arch: arch = "amd64"
            if os not in ("windows", "linux", "macos"):
                return "Error: gopher OS must be 'windows', 'linux', or 'macos'."
            if arch not in ("amd64", "arm64"):
                return "Error: gopher arch must be 'amd64' or 'arm64'."

        # Build config JSON
        config_dict = {"os": os, "arch": arch}
        if agent_name == "beacon":
            config_dict["format"] = format
            config_dict["sleep"] = str(sleep)
            config_dict["jitter"] = jitter
            config_dict["iat_hiding"] = iat_hiding
            if user_agent:
                config_dict["user_agent"] = user_agent
            if rotation_mode:
                config_dict["rotation_mode"] = rotation_mode
        elif agent_name == "gopher":
            config_dict["reconn_timeout"] = "60s"
            config_dict["reconn_count"] = 9999

        # Merge extra config JSON if provided
        if extra_config:
            try:
                extra = json.loads(extra_config)
                config_dict.update(extra)
            except json.JSONDecodeError as e:
                return f"Error: invalid extra_config JSON — {e}"

        config_json = json.dumps(config_dict)

        log.info("tool.generate_agent", listeners=listeners, agent=agent_name, config=config_json)
        try:
            filename, content = await ctx.client.generate_agent(listeners, agent_name, config_json)
            b64 = base64.b64encode(content).decode()
            return (
                f"Agent payload generated:\n"
                f"  Filename: {filename}\n"
                f"  Size: {len(content)} bytes\n"
                f"  Type: {agent_name} ({os}/{arch})\n"
                f"  Listeners: {', '.join(listeners)}\n"
                f"  Content (base64):\n{b64}"
            )
        except AdaptixAPIError as e:
            return f"Failed to generate agent: {e}"

    # ── Chat ────────────────────────────────────────────────────────────────

    @mcp.tool(description=(
        "Send a chat message visible in the AdaptixC2 teamserver console.\n"
        "Useful for broadcasting messages to other operators.\n"
        "Args: message (STR) — the chat text."
    ))
    async def send_chat(message: str) -> str:
        message = validate_nonempty(message, "message")
        log.info("tool.send_chat", message=message[:80])
        try:
            await ctx.client.send_chat(message)
            return f"Chat message sent."
        except AdaptixAPIError as e:
            return f"Failed to send chat: {e}"
