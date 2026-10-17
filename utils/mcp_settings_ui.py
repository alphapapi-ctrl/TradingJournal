"""MCP status and explicit startup setup, kept separate from account settings."""
from __future__ import annotations

import getpass
import socket
from pathlib import Path

import streamlit as st

from utils import mcp_control as control
from utils.mcp_config import CONFIG_PATH, endpoint, load_config, normalise


@st.cache_data(ttl=30, show_spinner=False)
def _status(config):
    return control.probe_health(config), control.task_status(config)


@st.cache_data(ttl=300, show_spinner=False)
def _requirements():
    return control.prerequisites()


def show():
    import database

    st.subheader("MCP / AI Access")
    st.caption(f"Controls the journal computer: {socket.gethostname()} · Windows user: {getpass.getuser()}")
    st.write("Connect your AI application over the local network. The login task starts MCP quietly alongside the journal and MT5.")
    if notice := st.session_state.pop("mcp_notice", None):
        st.success(notice)
    try:
        config = load_config()
    except ValueError as exc:
        st.error(str(exc))
        st.info(f"Correct or rename {CONFIG_PATH} and refresh this page.")
        return

    if st.button("Refresh MCP status", key="mcp_refresh"):
        _status.clear()
        _requirements.clear()
    health, task = _status(config)
    required = _requirements()
    left, right = st.columns(2)
    left.metric("MCP server", "Running" if health["running"] else "Not reachable")
    right.metric("Login task", task["state"])
    if task.get("error"):
        st.warning("The login task could not be checked. " + task["error"])
    if health["running"]:
        server = health["server"]
        st.caption(f"Server: {server.get('hostname', 'unknown')} · PID: {server.get('pid', 'unknown')}")
        active_db = server.get("database")
        st.text("MCP database: " + (active_db or "Unknown — restart MCP after updating to see the database path"))
        if active_db and Path(active_db).resolve() != Path(database.DB_PATH).resolve():
            st.error("MCP is using a different database from this journal. Stop MCP and reinstall the login task to use the journal database below.")
        if not health.get("alias_accepted"):
            st.warning("The running server rejects the connection hostname. Install/update the task with this hostname, then restart MCP.")
    else:
        st.caption("Start MCP below, then refresh. Details: " + health.get("error", ""))
    st.text("Journal database: " + str(Path(database.DB_PATH).resolve()))
    st.code(endpoint(config), language=None)
    st.caption("Use this URL in the AI application's MCP connection. Authentication: none. Health check: " + endpoint(config, health=True))
    if task.get("installed"):
        st.caption(f"Task: {task['task_name']} · Account: {task.get('user', 'unknown')} · Last result: {task.get('last_result', 'unknown')}")
        start_col, stop_col = st.columns(2)
        if start_col.button("Start MCP task", key="mcp_start"):
            _change("Start", config)
        if stop_col.button("Stop MCP task", key="mcp_stop"):
            _change("Stop", config)

    st.markdown("**Startup setup**")
    st.caption("Run setup on the journal computer under the Windows account that runs your journal and MT5. It starts 15 seconds after that account logs in; signing out stops availability. Locking the desktop is fine.")
    with st.form("mcp_startup_setup"):
        connection_host = st.text_input("Connection hostname or LAN IP", value=config["connection_host"], help="For your remote machine, use mt5tools or 192.168.2.234. Enter the hostname without http:// or a port.")
        aliases = st.text_input("Additional hostname aliases (comma separated)", value=", ".join(h for h in config["allowed_hosts"] if h != config["connection_host"]))
        col1, col2 = st.columns(2)
        port = col1.number_input("MCP port", min_value=1, max_value=65535, value=config["port"], step=1)
        bind = col2.text_input("Listen address", value=config["bind_address"], help="0.0.0.0 listens on this computer's network interfaces. 127.0.0.1 permits only this computer.")
        st.caption(f"Login task name: {config['task_name']} · Uses the journal database shown above.")
        start_now = st.checkbox("Start MCP immediately after setup", value=True)
        install = st.form_submit_button("Install / update MCP login task", disabled=not all(required.get(k) for k in ("windows", "python", "script", "dependencies")))
    try:
        proposed = normalise({**config, "connection_host": connection_host, "allowed_hosts": [h.strip() for h in aliases.split(",") if h.strip()],
                              "port": int(port), "bind_address": bind, "database": str(Path(database.DB_PATH).resolve())})
    except ValueError as exc:
        st.error(str(exc))
        proposed = None
    if install and proposed:
        _change("Install", proposed, start_now)

    with st.expander("Setup requirements and command-line alternative", expanded=not required["dependencies"]):
        for label, key in (("Windows on the journal computer", "windows"), ("Journal .venv with python.exe and pythonw.exe", "python"),
                           ("MCP setup script available", "script"), ("Optional MCP dependencies installed", "dependencies")):
            st.write(("✓ " if required[key] else "○ ") + label)
        st.write("Install the journal's normal dependencies first. Add the optional MCP dependencies once from the journal folder:")
        st.code(r".\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt", language="powershell")
        st.write("Allow the MCP TCP port through Windows Firewall on the private LAN. A hostname such as mt5tools must resolve to the journal machine's IP on each client PC. The Host check above does not test the client's DNS or firewall.")
        st.write("Setup stores no Windows password and requests no administrator rights. If Windows denies task registration, run the command below in an elevated PowerShell under the same journal account.")
        if proposed:
            st.code(control.setup_command(proposed), language="powershell")
        st.caption("Stop an existing login task before updating. A server started manually must also be stopped before applying a new port, alias or database. Start the task after updating, then refresh status.")
        st.text("Startup settings: " + str(CONFIG_PATH))
        st.text("Logs: " + str(Path(config["database"]).parent / "mcp"))
        st.caption("Low-priority MCP server plus a small sleeping supervisor. No model, periodic reports or trading automation runs here. Startup settings stay on this machine and are excluded from Git.")


def _change(action: str, config: dict, start_now: bool = False):
    try:
        result = control.manage_task(action, config, start_now)
    except Exception as exc:
        st.error(str(exc))
        return
    _status.clear()
    st.session_state["mcp_notice"] = f"{action} completed. Login task: {result['state']}. Startup can take a few seconds; refresh status to confirm."
    st.rerun()
