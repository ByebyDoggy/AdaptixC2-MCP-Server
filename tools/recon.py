"""
tools/recon.py — Reconnaissance tools.

Covers:
  - Native beacon commands: getuid, ps (list/kill/run), credentials, targets
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from tools._context  import ToolContext
from tools._helpers  import exec_cmd
from client.adaptix_client import AdaptixAPIError
from utils.validation import validate_nonempty
from utils.logging   import get_logger

log = get_logger("tools.recon")


def register_recon_tools(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register recon tools: native commands."""

    # ── Native beacon recon commands ──────────────────────────────────────────

    @mcp.tool(description=(
        "Get the current user identity on the agent.\n"
        "Runs native 'getuid' beacon command.\n"
        "Returns: username and privilege level of the current token."
    ))
    async def get_uid(agent_id: str) -> str:
        return await exec_cmd(ctx, agent_id, "getuid",
                              {"command": "getuid", "message": "Task: get username"},
                              log_name="getuid")

    @mcp.tool(description=(
        "List all running processes on the agent host.\n"
        "Runs 'ps list' beacon command.\n"
        "Returns: process list with PID, name, user, and session info."
    ))
    async def list_processes(agent_id: str) -> str:
        return await exec_cmd(ctx, agent_id, "ps list",
                              {"command": "ps", "subcommand": "list",
                               "message": "Task: show process list"},
                              log_name="ps.list")

    @mcp.tool(description=(
        "Kill a running process by PID.\n"
        "Args: pid (INT, required) — process ID to terminate."
    ))
    async def kill_process(agent_id: str, pid: int) -> str:
        return await exec_cmd(ctx, agent_id, f"ps kill {pid}",
                              {"command": "ps", "subcommand": "kill",
                               "pid": pid, "message": "Task: kill process"},
                              log_name="ps.kill")

    @mcp.tool(description=(
        "Run a program on the agent host via 'ps run'.\n"
        "Args:\n"
        "  args: Full path + arguments, e.g. 'C:\\\\Windows\\\\System32\\\\cmd.exe /c whoami'\n"
        "  suspend: Start process suspended (-s)\n"
        "  with_output: Capture output (-o)\n"
        "  impersonate: Use token impersonation (-i)"
    ))
    async def run_process(
        agent_id:    str,
        args:        str,
        suspend:     bool = False,
        with_output: bool = True,
        impersonate: bool = False,
    ) -> str:
        flags = ""
        if suspend:     flags += " -s"
        if with_output: flags += " -o"
        if impersonate: flags += " -i"
        data: dict = {"command": "ps", "subcommand": "run", "args": args}
        if suspend:     data["-s"] = True
        if with_output: data["-o"] = True
        if impersonate: data["-i"] = True
        return await exec_cmd(ctx, agent_id, f"ps run{flags} {args}", data, log_name="ps.run")

    @mcp.tool(description="List all credentials harvested across all agents.")
    async def list_credentials() -> str:
        creds = await ctx.client.list_creds_raw()
        if not creds:
            return "No credentials stored."
        lines = [f"Found {len(creds)} credential(s):"]
        for c in creds:
            user  = c.get("c_username", "?")
            realm = c.get("c_realm", "")
            host  = c.get("c_host", "")
            tag   = c.get("c_tag", "")
            lines.append(f"  {realm}\\{user} @ {host} [{tag}]")
        return "\n".join(lines)

    @mcp.tool(description="List all known targets/hosts in the teamserver database.")
    async def list_targets() -> str:
        targets = await ctx.client.list_targets_raw()
        if not targets:
            return "No targets stored."
        lines = [f"Found {len(targets)} target(s):"]
        for t in targets:
            comp   = t.get("t_computer", "?")
            addr   = t.get("t_address", "?")
            domain = t.get("t_domain", "")
            alive  = t.get("t_alive", False)
            agents = ", ".join(t.get("t_agents") or [])
            status = "ALIVE" if alive else "DEAD "
            lines.append(f"  [{status}] {domain}\\{comp} ({addr}) agents=[{agents}]")
        return "\n".join(lines)

    # ── Screenshots ─────────────────────────────────────────────────────────

    @mcp.tool(description="List all screenshots captured from agents.")
    async def list_screenshots() -> str:
        screenshots = await ctx.client.list_screenshots_raw()
        if not screenshots:
            return "No screenshots available."
        lines = [f"Found {len(screenshots)} screenshot(s):"]
        for s in screenshots:
            sid  = s.get("s_id", s.get("screen_id", "?"))
            agent = s.get("s_aid", s.get("agent_id", "?"))
            desc = s.get("s_desc", s.get("description", ""))
            fmt  = s.get("s_format", s.get("format", "?"))
            note = s.get("s_note", s.get("note", ""))
            lines.append(f"  [{sid}] Agent={agent} {desc} ({fmt}) note={note!r}")
        return "\n".join(lines)

    @mcp.tool(description=(
        "Retrieve a screenshot image as base64-encoded PNG.\n"
        "Use list_screenshots first to get the screen_id.\n"
        "Args: screen_id (STR)."
    ))
    async def get_screenshot(screen_id: str) -> str:
        import base64
        screen_id = validate_nonempty(screen_id, "screen_id")
        log.info("tool.get_screenshot", screen_id=screen_id)
        try:
            png_bytes = await ctx.client.get_screenshot_image(screen_id)
            b64 = base64.b64encode(png_bytes).decode()
            return (
                f"Screenshot ({screen_id}):\n"
                f"  Size: {len(png_bytes)} bytes\n"
                f"  Format: PNG\n"
                f"  Content (base64):\n{b64}"
            )
        except AdaptixAPIError as e:
            return f"Failed to get screenshot: {e}"

    # ── Credential management ───────────────────────────────────────────────

    @mcp.tool(description=(
        "Add credentials to the teamserver database.\n"
        "Args: creds_json (STR) — JSON array of credential objects.\n"
        "Each object can have keys: username, password, realm, type, tag, storage, host.\n"
        "Example: add_creds('[{\"username\":\"admin\",\"password\":\"P@ss123\",\"realm\":\"DOMAIN\"}]')"
    ))
    async def add_creds(creds_json: str) -> str:
        import json
        try:
            creds = json.loads(creds_json)
            if not isinstance(creds, list):
                return "Error: creds_json must be a JSON array."
            if not creds:
                return "Error: empty credentials list."
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON — {e}"
        log.info("tool.add_creds", count=len(creds))
        try:
            await ctx.client.add_creds(creds)
            return f"{len(creds)} credential(s) added to database."
        except AdaptixAPIError as e:
            return f"Failed to add credentials: {e}"

    # ── Target management ───────────────────────────────────────────────────

    @mcp.tool(description=(
        "Add target hosts to the teamserver database.\n"
        "Args: targets_json (STR) — JSON array of target objects.\n"
        "Each object can have keys: t_computer, t_address, t_domain, t_os, t_version.\n"
        "Example: add_targets('[{\"t_computer\":\"DC01\",\"t_address\":\"192.168.1.10\",\"t_domain\":\"DOMAIN\"}]')"
    ))
    async def add_targets(targets_json: str) -> str:
        import json
        try:
            targets = json.loads(targets_json)
            if not isinstance(targets, list):
                return "Error: targets_json must be a JSON array."
            if not targets:
                return "Error: empty targets list."
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON — {e}"
        log.info("tool.add_targets", count=len(targets))
        try:
            await ctx.client.add_targets(targets)
            return f"{len(targets)} target(s) added to database."
        except AdaptixAPIError as e:
            return f"Failed to add targets: {e}"
