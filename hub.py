print("HUB.PY IS EXECUTING!")
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for, session
import threading
from rich.logging import RichHandler
import os
from waitress import serve
import json # ADD json for pretty printing
import signal # ADD signal for process termination
import sys # For sys.executable in subprocess calls
import atexit # For cleanup on hub exit
import uuid # NEW: For unique approval request IDs
import secrets # For webhook secret tokens
import time # Ensure time is imported
import hashlib # For password hashing
from dotenv import load_dotenv, set_key # Import load_dotenv and set_key
import zipfile # NEW: For handling zip file uploads
import shutil # NEW: For directory operations (e.g., rmtree)
from werkzeug.utils import secure_filename # NEW: For securing uploaded filenames
from pathlib import Path # NEW: For path manipulation
import requests # IMPORTED: Add requests for HTTP communication
import subprocess as _subprocess  # For update system git/pip commands
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

# Configure logging for the hub
log_file_path = os.path.join(os.path.dirname(__file__), "agent_debug.log")
logging.basicConfig(
    level="DEBUG", format="%(message)s", datefmt="[%X]", handlers=[RichHandler(), logging.FileHandler(log_file_path)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.cache = {}
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# ─── Authentication System ───

AUTH_FILE = os.path.join(os.path.dirname(__file__), "auth.json")

def _load_auth():
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}  # Empty = no account created yet (first run)

def _save_auth(auth_data):
    with open(AUTH_FILE, "w") as f:
        json.dump(auth_data, f, indent=2)

def _hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

_auth_config = _load_auth()

def _is_first_run():
    """True if no admin account has been set up yet."""
    return not _auth_config.get("username")

# Routes that agents use (no auth required)
AGENT_API_PREFIXES = (
    "/register_agent", "/heartbeat", "/submit_experience",
    "/receive_user_message", "/webhook/", "/get_pending_messages",
    "/api/agent_health", "/static/"
)

@app.before_request
def _check_auth():
    path = request.path

    # Always allow agent-to-hub API routes
    for prefix in AGENT_API_PREFIXES:
        if path.startswith(prefix):
            return None

    # Always allow setup, login, logout, favicon
    if path in ("/setup", "/login", "/logout", "/favicon.ico"):
        return None

    # First run — force setup before anything else
    if _is_first_run():
        if path.startswith("/api/"):
            return jsonify({"status": "error", "message": "Initial setup required. Visit the dashboard to create your admin account."}), 401
        return redirect(url_for("setup_page"))

    # Auth enabled — check session
    if _auth_config.get("enabled", True):
        if session.get("authenticated"):
            return None
        if path.startswith("/api/") or request.is_json:
            return jsonify({"status": "error", "message": "Authentication required."}), 401
        return redirect(url_for("login_page"))

    return None

@app.route("/setup", methods=["GET", "POST"])
def setup_page():
    """First-run setup: create admin username and password."""
    if not _is_first_run():
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()
        if not username:
            error = "Username is required."
        elif not password:
            error = "Password is required."
        elif len(password) < 4:
            error = "Password must be at least 4 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            global _auth_config
            _auth_config = {
                "username": username,
                "password_hash": _hash_password(password),
                "enabled": True,
                "created_at": time.time()
            }
            _save_auth(_auth_config)
            session["authenticated"] = True
            logger.info(f"[Auth] Admin account created: {username}")
            return redirect(url_for("dashboard"))
    return render_template("setup.html", error=error)

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if _is_first_run():
        return redirect(url_for("setup_page"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if (username == _auth_config.get("username", "") and
                _hash_password(password) == _auth_config.get("password_hash", "")):
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login_page"))

@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    return jsonify({
        "status": "success",
        "enabled": _auth_config.get("enabled", False),
        "username": _auth_config.get("username", ""),
        "setup_complete": not _is_first_run()
    }), 200

@app.route("/api/auth/set_password", methods=["POST"])
def api_set_password():
    global _auth_config
    data = request.json or {}
    new_password = data.get("password", "").strip()
    new_username = data.get("username", "").strip()
    enabled = data.get("enabled", _auth_config.get("enabled", True))
    if enabled and not new_password and not _auth_config.get("password_hash"):
        return jsonify({"status": "error", "message": "Password is required to enable authentication."}), 400
    if new_password:
        if len(new_password) < 4:
            return jsonify({"status": "error", "message": "Password must be at least 4 characters."}), 400
        _auth_config["password_hash"] = _hash_password(new_password)
    if new_username:
        _auth_config["username"] = new_username
    _auth_config["enabled"] = bool(enabled)
    _save_auth(_auth_config)
    session["authenticated"] = True
    return jsonify({"status": "success", "enabled": _auth_config["enabled"], "message": "Authentication settings updated."}), 200

hub_memory = {
    "experiences": [],
    "distilled_tips": [],
    "active_agents": {},
    "agent_message_queues": {},
    "recent_experiences": [],
    "MAX_RECENT_EXPERIENCES": 20,
    "user_messages": [],
    "pending_approvals": {}, # NEW: For handling approvals
    "total_token_usage": { # NEW: To track aggregated token usage
        "moonshot_ai": {"prompt_tokens": 0, "completion_tokens": 0},
        "ollama": {"prompt_tokens": 0, "completion_tokens": 0},
        "openrouter": {"prompt_tokens": 0, "completion_tokens": 0},
        "voyage_ai": {"prompt_tokens": 0, "completion_tokens": 0},
        "huggingface": {"prompt_tokens": 0, "completion_tokens": 0}
    },
    "per_agent_token_usage": {},  # {"AgentName": {"openrouter": {"prompt_tokens": 0, "completion_tokens": 0}, ...}}
    "global_plugin_preferences": {}, # NEW: To store global enable/disable status for plugins
    "global_auto_install_deps": True, # NEW: Global setting for auto-installing plugin dependencies
    "discovered_plugins": {}, # NEW: To store details of all plugins found in the plugins directory
    "scheduled_tasks": [], # Scheduled/cron tasks (persisted to scheduled_tasks.json)
    "webhooks": {},  # {"<id>": {"id", "name", "agent_name", "secret", "created_at", "enabled", "last_triggered", "trigger_count"}}
    "notifications": [],  # [{"id", "type", "message", "timestamp", "read"}] — capped at 50
    "agent_templates": [],  # Persisted to agent_templates.json
    "workflows": [],        # Persisted to workflows.json
    "workflow_runs": [],    # Persisted to workflow_runs.json
    "agent_groups": {}      # {"group_name": {"name", "agents": [], "created_at"}} — Persisted to agent_groups.json
}

launched_agent_processes = {}
launched_agent_configs = {}  # {agent_name: {"cmd": [...], "env": {...}}} — for auto-restart
auto_restart_enabled = True

shutdown_event = threading.Event()

waitress_server = None

def _cleanup_agent_processes():
    """Kill all launched agent processes on hub exit (Ctrl+C, SIGTERM, etc.)."""
    for agent_name, pid in list(launched_agent_processes.items()):
        try:
            os.killpg(pid, signal.SIGTERM)
            logger.info(f"Cleanup: Terminated agent '{agent_name}' (PID: {pid})")
        except ProcessLookupError:
            pass  # Already dead
        except Exception as e:
            logger.error(f"Cleanup: Error killing agent '{agent_name}' (PID: {pid}): {e}")
    launched_agent_processes.clear()

atexit.register(_cleanup_agent_processes)

def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM to ensure agent cleanup before exit."""
    logger.info(f"Received signal {signum}, cleaning up agent processes...")
    _cleanup_agent_processes()
    sys.exit(0)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _auto_restart_monitor():
    """Background thread: periodically checks if any launched agent processes have died and restarts them."""
    while not shutdown_event.is_set():
        shutdown_event.wait(30)  # Check every 30 seconds
        if shutdown_event.is_set() or not auto_restart_enabled:
            continue
        for agent_name, pid in list(launched_agent_processes.items()):
            try:
                os.kill(pid, 0)  # Check if process is alive (signal 0 = no-op)
            except ProcessLookupError:
                # Process is dead — try to restart
                config = launched_agent_configs.get(agent_name)
                if not config:
                    logger.warning(f"[AutoRestart] Agent '{agent_name}' (PID {pid}) died but no launch config saved — cannot restart.")
                    launched_agent_processes.pop(agent_name, None)
                    continue
                logger.warning(f"[AutoRestart] Agent '{agent_name}' (PID {pid}) died — restarting...")
                try:
                    cmd = config["cmd"]
                    env = config["env"]
                    stdout_path = os.path.join(os.path.dirname(__file__), f"agent_{agent_name}_stdout.log")
                    stderr_path = os.path.join(os.path.dirname(__file__), f"agent_{agent_name}_stderr.log")
                    with open(stdout_path, "a") as stdout_f, open(stderr_path, "a") as stderr_f:
                        with open(os.devnull, 'r') as devnull:
                            process = subprocess.Popen(cmd, stdin=devnull, stdout=stdout_f, stderr=stderr_f, preexec_fn=os.setsid, env=env)
                            launched_agent_processes[agent_name] = process.pid
                    logger.info(f"[AutoRestart] Agent '{agent_name}' restarted (new PID: {process.pid})")
                    _add_notification("agent_restarted", f"Agent '{agent_name}' crashed and was auto-restarted")
                except Exception as e:
                    logger.error(f"[AutoRestart] Failed to restart agent '{agent_name}': {e}")
                    launched_agent_processes.pop(agent_name, None)
            except PermissionError:
                pass  # Process exists but we can't signal it — leave it


_auto_restart_thread = threading.Thread(target=_auto_restart_monitor, daemon=True)
_auto_restart_thread.start()


def _discover_all_plugins():
    """
    Scans the plugins directory for all available plugins and stores their manifest data.
    """
    logger.info("Starting plugin discovery...")
    plugins_dir = Path(__file__).parent / "plugins"
    if not plugins_dir.is_dir():
        logger.info(f"Plugins directory not found at {plugins_dir}. No plugins will be discovered.")
        return

    logger.info(f"Discovering all plugins in {plugins_dir}...")
    for plugin_path in plugins_dir.iterdir():
        if plugin_path.is_dir():
            manifest_path = plugin_path / "manifest.json"
            if manifest_path.is_file():
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                    plugin_name = manifest.get('name', plugin_path.name)
                    hub_memory["discovered_plugins"][plugin_name] = {
                        "name": plugin_name,
                        "description": manifest.get("description", "No description provided."),
                        "version": manifest.get("version", "N/A"),
                        "author": manifest.get("author", "Unknown"),
                        "path": str(plugin_path),
                        "status": hub_memory["global_plugin_preferences"].get(plugin_name, "enabled") # Reflect global preference
                    }
                    logger.info(f"Discovered plugin: {plugin_name}")
                except json.JSONDecodeError:
                    logger.error(f"Invalid manifest.json in plugin '{plugin_path.name}'. Skipping discovery.")
                except Exception as e:
                    logger.error(f"Error discovering plugin '{plugin_path.name}': {e}", exc_info=True)
    logger.info(f"Finished plugin discovery. Found {len(hub_memory['discovered_plugins'])} plugins.")

# Initial plugin discovery when the hub starts
_discover_all_plugins()

# ─── Scheduled Tasks System ───

_scheduler_timezone = os.environ.get("SCHEDULER_TIMEZONE", "UTC")
_scheduler = BackgroundScheduler(timezone=_scheduler_timezone)
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown(wait=False))

SCHEDULED_TASKS_FILE = os.path.join(os.path.dirname(__file__), "scheduled_tasks.json")

def _execute_scheduled_task(task_id, agent_name, task_message):
    """APScheduler callback — drops a message into the agent's queue."""
    logger.info(f"[Scheduler] Firing task '{task_id}' for agent '{agent_name}': {task_message[:80]}")
    if agent_name not in hub_memory["agent_message_queues"]:
        hub_memory["agent_message_queues"][agent_name] = []
    hub_memory["agent_message_queues"][agent_name].append({
        "sender": "scheduler",
        "message": task_message
    })
    _add_notification("scheduled_task_fired", f"Scheduled task fired for '{agent_name}': {task_message[:60]}")
    # Update last_run
    for t in hub_memory["scheduled_tasks"]:
        if t["id"] == task_id:
            t["last_run"] = time.time()
            break
    _save_scheduled_tasks()

def _save_scheduled_tasks():
    """Persist scheduled tasks to disk."""
    try:
        with open(SCHEDULED_TASKS_FILE, "w") as f:
            json.dump(hub_memory["scheduled_tasks"], f, indent=2)
    except Exception as e:
        logger.error(f"[Scheduler] Error saving tasks: {e}")

def _register_task_with_scheduler(task):
    """Register a single task dict with APScheduler."""
    try:
        if task["schedule_type"] == "cron":
            tz = os.environ.get("SCHEDULER_TIMEZONE", "UTC")
            trigger = CronTrigger(
                hour=int(task.get("cron_hour", 9)),
                minute=int(task.get("cron_minute", 0)),
                day_of_week=task.get("cron_days", "*"),
                timezone=tz
            )
        else:
            trigger = IntervalTrigger(seconds=int(task.get("interval_seconds", 3600)))

        _scheduler.add_job(
            _execute_scheduled_task,
            trigger=trigger,
            args=[task["id"], task["agent_name"], task["task_message"]],
            id=task["id"],
            name=f"{task['agent_name']}: {task['task_message'][:50]}",
            replace_existing=True
        )
    except Exception as e:
        logger.error(f"[Scheduler] Error registering task '{task['id']}': {e}")

def _load_scheduled_tasks():
    """Load tasks from disk and register them with APScheduler."""
    if os.path.exists(SCHEDULED_TASKS_FILE):
        try:
            with open(SCHEDULED_TASKS_FILE, "r") as f:
                hub_memory["scheduled_tasks"] = json.load(f)
            for task in hub_memory["scheduled_tasks"]:
                if task.get("enabled", True):
                    _register_task_with_scheduler(task)
            logger.info(f"[Scheduler] Loaded {len(hub_memory['scheduled_tasks'])} scheduled task(s) from disk.")
        except Exception as e:
            logger.error(f"[Scheduler] Error loading tasks: {e}")

_load_scheduled_tasks()

# ─── Notification System ───

MAX_NOTIFICATIONS = 50
NOTIFICATIONS_FILE = os.path.join(os.path.dirname(__file__), "notifications.json")

def _save_notifications():
    try:
        with open(NOTIFICATIONS_FILE, "w") as f:
            json.dump(hub_memory["notifications"], f, indent=2)
    except Exception as e:
        logger.error(f"[Notifications] Error saving: {e}")

def _load_notifications():
    if os.path.exists(NOTIFICATIONS_FILE):
        try:
            with open(NOTIFICATIONS_FILE, "r") as f:
                hub_memory["notifications"] = json.load(f)
            logger.info(f"[Notifications] Loaded {len(hub_memory['notifications'])} notification(s) from disk.")
        except Exception as e:
            logger.error(f"[Notifications] Error loading: {e}")

_load_notifications()

def _add_notification(ntype, message):
    """Add a notification to the feed (capped at MAX_NOTIFICATIONS, persisted to disk)."""
    hub_memory["notifications"].insert(0, {
        "id": str(uuid.uuid4())[:8],
        "type": ntype,
        "message": message,
        "timestamp": time.time(),
        "read": False
    })
    if len(hub_memory["notifications"]) > MAX_NOTIFICATIONS:
        hub_memory["notifications"] = hub_memory["notifications"][:MAX_NOTIFICATIONS]
    _save_notifications()

# ─── Webhook System ───

WEBHOOKS_FILE = os.path.join(os.path.dirname(__file__), "webhooks.json")

def _save_webhooks():
    try:
        with open(WEBHOOKS_FILE, "w") as f:
            json.dump(hub_memory["webhooks"], f, indent=2)
    except Exception as e:
        logger.error(f"[Webhooks] Error saving: {e}")

def _load_webhooks():
    if os.path.exists(WEBHOOKS_FILE):
        try:
            with open(WEBHOOKS_FILE, "r") as f:
                hub_memory["webhooks"] = json.load(f)
            logger.info(f"[Webhooks] Loaded {len(hub_memory['webhooks'])} webhook(s) from disk.")
        except Exception as e:
            logger.error(f"[Webhooks] Error loading: {e}")

_load_webhooks()

# ─── Agent Groups System ───

AGENT_GROUPS_FILE = os.path.join(os.path.dirname(__file__), "agent_groups.json")

def _save_agent_groups():
    try:
        with open(AGENT_GROUPS_FILE, "w") as f:
            json.dump(hub_memory["agent_groups"], f, indent=2)
    except Exception as e:
        logger.error(f"[Groups] Error saving: {e}")

def _load_agent_groups():
    if os.path.exists(AGENT_GROUPS_FILE):
        try:
            with open(AGENT_GROUPS_FILE, "r") as f:
                hub_memory["agent_groups"] = json.load(f)
            logger.info(f"[Groups] Loaded {len(hub_memory['agent_groups'])} group(s) from disk.")
        except Exception as e:
            logger.error(f"[Groups] Error loading: {e}")

_load_agent_groups()

# ─── Agent Templates System ───

AGENT_TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "agent_templates.json")

def _save_agent_templates():
    try:
        with open(AGENT_TEMPLATES_FILE, "w") as f:
            json.dump(hub_memory["agent_templates"], f, indent=2)
    except Exception as e:
        logger.error(f"[Templates] Error saving: {e}")

def _load_agent_templates():
    if os.path.exists(AGENT_TEMPLATES_FILE):
        try:
            with open(AGENT_TEMPLATES_FILE, "r") as f:
                hub_memory["agent_templates"] = json.load(f)
            logger.info(f"[Templates] Loaded {len(hub_memory['agent_templates'])} template(s) from disk.")
        except Exception as e:
            logger.error(f"[Templates] Error loading: {e}")

_load_agent_templates()

# ─── Workflow Engine ───

WORKFLOWS_FILE = os.path.join(os.path.dirname(__file__), "workflows.json")
WORKFLOW_RUNS_FILE = os.path.join(os.path.dirname(__file__), "workflow_runs.json")

def _save_workflows():
    try:
        with open(WORKFLOWS_FILE, "w") as f:
            json.dump(hub_memory["workflows"], f, indent=2)
    except Exception as e:
        logger.error(f"[Workflows] Error saving: {e}")

def _load_workflows():
    if os.path.exists(WORKFLOWS_FILE):
        try:
            with open(WORKFLOWS_FILE, "r") as f:
                hub_memory["workflows"] = json.load(f)
            logger.info(f"[Workflows] Loaded {len(hub_memory['workflows'])} workflow(s) from disk.")
        except Exception as e:
            logger.error(f"[Workflows] Error loading: {e}")

def _save_workflow_runs():
    try:
        with open(WORKFLOW_RUNS_FILE, "w") as f:
            json.dump(hub_memory["workflow_runs"], f, indent=2)
    except Exception as e:
        logger.error(f"[Workflow Runs] Error saving: {e}")

def _load_workflow_runs():
    if os.path.exists(WORKFLOW_RUNS_FILE):
        try:
            with open(WORKFLOW_RUNS_FILE, "r") as f:
                hub_memory["workflow_runs"] = json.load(f)
            logger.info(f"[Workflow Runs] Loaded {len(hub_memory['workflow_runs'])} run(s) from disk.")
        except Exception as e:
            logger.error(f"[Workflow Runs] Error loading: {e}")

_load_workflows()
_load_workflow_runs()

import re as _re

def _resolve_template(template_str, trigger_input, step_results):
    """Replace {{trigger_input}} and {{stepX.output}} with actual values."""
    result = template_str.replace("{{trigger_input}}", str(trigger_input or ""))
    # Replace {{stepX.output}} patterns
    def _step_replacer(match):
        step_id = match.group(1)
        if step_id in step_results and step_results[step_id].get("output"):
            return str(step_results[step_id]["output"])
        return match.group(0)  # Leave placeholder if no output yet
    result = _re.sub(r"\{\{(\w+)\.output\}\}", _step_replacer, result)
    return result

def _advance_workflow(run_id):
    """Advance a workflow run to its next step."""
    run = None
    for r in hub_memory["workflow_runs"]:
        if r["run_id"] == run_id:
            run = r
            break
    if not run or run["status"] not in ("running", "paused"):
        return

    # Find workflow definition
    workflow = None
    for w in hub_memory["workflows"]:
        if w["id"] == run["workflow_id"]:
            workflow = w
            break
    if not workflow:
        run["status"] = "failed"
        run["completed_at"] = time.time()
        _save_workflow_runs()
        _add_notification("workflow_failed", f"Workflow '{run['workflow_name']}' failed — workflow definition not found")
        return

    current_step_id = run["current_step_id"]
    # Find current step definition
    current_step_def = None
    for s in workflow["steps"]:
        if s["id"] == current_step_id:
            current_step_def = s
            break

    if not current_step_def:
        run["status"] = "failed"
        run["completed_at"] = time.time()
        _save_workflow_runs()
        _add_notification("workflow_failed", f"Workflow '{run['workflow_name']}' failed — step '{current_step_id}' not found")
        return

    step_result = run["step_results"].get(current_step_id, {})
    step_status = step_result.get("status", "pending")

    if step_status == "completed":
        next_step_id = current_step_def.get("next_step")
        if not next_step_id:
            # Workflow is done
            run["status"] = "completed"
            run["completed_at"] = time.time()
            _save_workflow_runs()
            _add_notification("workflow_completed", f"Workflow '{run['workflow_name']}' completed successfully")
            return

        # Find next step definition
        next_step_def = None
        for s in workflow["steps"]:
            if s["id"] == next_step_id:
                next_step_def = s
                break
        if not next_step_def:
            run["status"] = "failed"
            run["completed_at"] = time.time()
            _save_workflow_runs()
            _add_notification("workflow_failed", f"Workflow '{run['workflow_name']}' failed — next step '{next_step_id}' not found")
            return

        # Check if next step needs approval before running
        if next_step_def.get("wait_for_approval"):
            run["status"] = "paused"
            run["current_step_id"] = next_step_id
            run["step_results"][next_step_id] = {
                "status": "waiting_approval",
                "agent_name": next_step_def["agent_name"],
                "started_at": None,
                "completed_at": None,
                "output": None
            }
            _save_workflow_runs()
            _add_notification("workflow_paused", f"Workflow '{run['workflow_name']}' paused — awaiting approval for step '{next_step_def['name']}'")
            return

        # Execute next step
        _execute_workflow_step(run, workflow, next_step_def)

    elif step_status == "failed":
        on_failure = current_step_def.get("on_failure")
        if on_failure:
            # Jump to the on_failure step
            fail_step_def = None
            for s in workflow["steps"]:
                if s["id"] == on_failure:
                    fail_step_def = s
                    break
            if fail_step_def:
                _execute_workflow_step(run, workflow, fail_step_def)
                return
        # No recovery — workflow failed
        run["status"] = "failed"
        run["completed_at"] = time.time()
        _save_workflow_runs()
        _add_notification("workflow_failed", f"Workflow '{run['workflow_name']}' failed at step '{current_step_def['name']}'")

def _execute_workflow_step(run, workflow, step_def):
    """Send a task to the agent for a workflow step."""
    agent_name = step_def["agent_name"]
    step_id = step_def["id"]

    # Check if agent is active
    if agent_name not in hub_memory["active_agents"]:
        run["status"] = "failed"
        run["current_step_id"] = step_id
        run["step_results"][step_id] = {
            "status": "failed",
            "agent_name": agent_name,
            "started_at": time.time(),
            "completed_at": time.time(),
            "output": f"Agent '{agent_name}' is not running"
        }
        run["completed_at"] = time.time()
        _save_workflow_runs()
        _add_notification("workflow_failed", f"Workflow '{run['workflow_name']}' failed — agent '{agent_name}' is not running")
        return

    # Resolve template variables
    task_message = _resolve_template(step_def.get("task_template", ""), run.get("trigger_input", ""), run.get("step_results", {}))

    # Update run state
    run["status"] = "running"
    run["current_step_id"] = step_id
    run["step_results"][step_id] = {
        "status": "running",
        "agent_name": agent_name,
        "started_at": time.time(),
        "completed_at": None,
        "output": None
    }
    _save_workflow_runs()

    # Send task to agent queue
    if agent_name not in hub_memory["agent_message_queues"]:
        hub_memory["agent_message_queues"][agent_name] = []
    hub_memory["agent_message_queues"][agent_name].append({
        "sender": "workflow",
        "message": task_message
    })
    logger.info(f"[Workflow] Sent task to agent '{agent_name}' for step '{step_def['name']}' in workflow '{run['workflow_name']}'")

def _check_workflow_step_completion(agent_name, message):
    """Check if an agent's message completes a workflow step."""
    for run in hub_memory["workflow_runs"]:
        if run["status"] != "running":
            continue
        step_id = run.get("current_step_id")
        if not step_id:
            continue
        step_result = run["step_results"].get(step_id, {})
        if step_result.get("status") == "running" and step_result.get("agent_name") == agent_name:
            # This agent's output completes this workflow step
            step_result["status"] = "completed"
            step_result["completed_at"] = time.time()
            step_result["output"] = message
            _save_workflow_runs()
            step_name = step_id
            # Get step name from workflow def
            for w in hub_memory["workflows"]:
                if w["id"] == run["workflow_id"]:
                    for s in w["steps"]:
                        if s["id"] == step_id:
                            step_name = s["name"]
                            break
                    break
            _add_notification("workflow_step_completed", f"Workflow '{run['workflow_name']}': step '{step_name}' completed by agent '{agent_name}'")
            _advance_workflow(run["run_id"])
            return  # Only match first active run for this agent

def _check_workflow_step_failure(agent_name):
    """Check if an agent's failure affects a workflow step."""
    for run in hub_memory["workflow_runs"]:
        if run["status"] != "running":
            continue
        step_id = run.get("current_step_id")
        if not step_id:
            continue
        step_result = run["step_results"].get(step_id, {})
        if step_result.get("status") == "running" and step_result.get("agent_name") == agent_name:
            step_result["status"] = "failed"
            step_result["completed_at"] = time.time()
            _save_workflow_runs()
            _advance_workflow(run["run_id"])
            return

# -------------------- Routes --------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    # Convert timestamps to human-readable format for display
    display_agents = {}
    for name, details in hub_memory["active_agents"].items():
        display_agents[name] = {
            "url": details["url"],
            "last_heartbeat": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(details["last_heartbeat"]))
        }

    return render_template("dashboard.html", 
                           active_agents=display_agents, # Use display_agents for dashboard
                           user_messages=hub_memory["user_messages"],
                           distilled_tips=hub_memory["distilled_tips"],
                           recent_experiences=hub_memory["recent_experiences"],
                           pending_approvals=hub_memory["pending_approvals"],
                           discovered_plugins=hub_memory["discovered_plugins"])

@app.route("/register_agent", methods=["POST"])
def register_agent():
    data = request.json
    agent_name = data.get("name")
    agent_url = data.get("url")
    if agent_name and agent_url:
        hub_memory["active_agents"][agent_name] = {"url": agent_url, "last_heartbeat": time.time(), "registered_at": time.time()}
        hub_memory["agent_message_queues"][agent_name] = [] # Initialize message queue for new agent
        logger.info(f"Agent '{agent_name}' registered with URL: {agent_url}")
        return jsonify({"status": "success", "message": f"Agent {agent_name} registered."}), 200
    return jsonify({"status": "error", "message": "Invalid agent registration data."}), 400

@app.route("/api/heartbeat/<agent_name>", methods=["POST"])
def agent_heartbeat(agent_name):
    if agent_name in hub_memory["active_agents"]:
        data = request.json
        hub_memory["active_agents"][agent_name]["last_heartbeat"] = time.time()
        
        # NEW: Store plugin data from heartbeat
        plugins_data = data.get("plugins")
        if plugins_data:
            hub_memory["active_agents"][agent_name]["plugins"] = plugins_data
            # Initialize global plugin preferences for newly seen plugins
            for plugin_name, manifest in plugins_data.get("manifests", {}).items():
                if plugin_name not in hub_memory["global_plugin_preferences"]:
                    hub_memory["global_plugin_preferences"][plugin_name] = "enabled" # Default to enabled
                    logger.info(f"Discovered new plugin '{plugin_name}'. Defaulting to 'enabled'.")
        
        # NEW: Store Ollama models from heartbeat
        ollama_models = data.get("ollama_models")
        if ollama_models:
            hub_memory["active_agents"][agent_name]["ollama_models"] = ollama_models
            logger.debug(f"Received Ollama models for agent '{agent_name}': {ollama_models}")

        logger.debug(f"Received heartbeat from agent '{agent_name}'.")
        return jsonify({"status": "success", "message": f"Heartbeat received for {agent_name}."}), 200
    return jsonify({"status": "error", "message": f"Agent {agent_name} not found."}), 404

@app.route("/get_active_agents", methods=["GET"])
def get_active_agents():
    # Optionally, clean up inactive agents here based on last_heartbeat
    return jsonify(hub_memory["active_agents"]), 200

@app.route("/submit_experience", methods=["POST"])
def submit_experience():
    experiences = request.json
    for exp in experiences:
        hub_memory["experiences"].append(exp)
        hub_memory["recent_experiences"].append(exp)
        if len(hub_memory["recent_experiences"]) > hub_memory["MAX_RECENT_EXPERIENCES"]:
            hub_memory["recent_experiences"].pop(0) # Keep only the most recent experiences

        # Aggregate token usage if present in the experience
        if "token_usage" in exp and exp["token_usage"]:
            token_data = exp["token_usage"]
            agent_name = exp.get("agent_name", "unknown")

            # Ensure per-agent bucket exists
            if agent_name not in hub_memory["per_agent_token_usage"]:
                hub_memory["per_agent_token_usage"][agent_name] = {}

            def _add_tokens(provider, prompt_tokens, completion_tokens):
                """Add tokens to both global and per-agent tracking."""
                # Global
                if provider not in hub_memory["total_token_usage"]:
                    hub_memory["total_token_usage"][provider] = {"prompt_tokens": 0, "completion_tokens": 0}
                hub_memory["total_token_usage"][provider]["prompt_tokens"] += prompt_tokens
                hub_memory["total_token_usage"][provider]["completion_tokens"] += completion_tokens
                # Per-agent
                agent_bucket = hub_memory["per_agent_token_usage"][agent_name]
                if provider not in agent_bucket:
                    agent_bucket[provider] = {"prompt_tokens": 0, "completion_tokens": 0}
                agent_bucket[provider]["prompt_tokens"] += prompt_tokens
                agent_bucket[provider]["completion_tokens"] += completion_tokens

            # Handle flat format: {"provider": "ollama", "prompt_tokens": 10, ...}
            if "provider" in token_data and token_data["provider"] != "none":
                _add_tokens(token_data["provider"], token_data.get("prompt_tokens", 0), token_data.get("completion_tokens", 0))
                logger.debug(f"Aggregated token usage for {token_data['provider']} (agent={agent_name})")
            else:
                # Handle nested format: {"ollama": {"prompt_tokens": 10, ...}, "openrouter": {...}}
                for provider, usage in token_data.items():
                    if isinstance(usage, dict) and (usage.get("prompt_tokens", 0) > 0 or usage.get("completion_tokens", 0) > 0):
                        _add_tokens(provider, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
                        logger.debug(f"Aggregated token usage for {provider} (agent={agent_name})")

        # Notify on errors
        step_result = exp.get("step_result", {})
        if step_result.get("status") == "failed":
            exp_agent = exp.get("agent_name", "unknown")
            _add_notification("task_error", f"Agent '{exp_agent}' encountered an error")
            # Check if this failure affects a workflow step
            _check_workflow_step_failure(exp_agent)

    logger.debug(f"Received {len(experiences)} experiences.")
    return jsonify({"status": "success", "message": "Experiences submitted."}), 200

@app.route("/shutdown_agent/<agent_name>", methods=["POST"])
def shutdown_agent(agent_name):
    if agent_name in hub_memory["active_agents"]:
        agent_url = hub_memory["active_agents"][agent_name]["url"] # Get the agent's URL
        
        # Terminate the agent process if it was launched by this hub
        process_terminated_successfully = False
        if agent_name in launched_agent_processes:
            pid = launched_agent_processes.pop(agent_name)
            launched_agent_configs.pop(agent_name, None)  # Remove config so auto-restart won't revive it
            try:
                os.killpg(pid, signal.SIGTERM) # Use SIGTERM for graceful shutdown
                logger.info(f"Terminated agent process group for '{agent_name}' (PID: {pid}).")
                process_terminated_successfully = True
            except ProcessLookupError:
                logger.warning(f"Agent process for '{agent_name}' (PID: {pid}) not found, already terminated.")
                process_terminated_successfully = True # Consider it terminated if not found
            except Exception as e:
                logger.error(f"Error terminating agent process group for '{agent_name}' (PID: {pid}): {e}")
        
        # Deregister from hub's memory only if process termination was attempted (and possibly succeeded)
        # This prevents 404 errors if the process lingers but hub thinks it's gone
        if agent_name in hub_memory["active_agents"]:
            del hub_memory["active_agents"][agent_name]
        if agent_name in hub_memory["agent_message_queues"]:
            del hub_memory["agent_message_queues"][agent_name]
        logger.info(f"Agent '{agent_name}' deregistered from hub's memory.")

        status_message = f"Agent {agent_name} deregistered from hub."
        if process_terminated_successfully:
            status_message += " Process termination attempted and likely successful."
        else:
            status_message += " Process termination failed or was not applicable."

        _add_notification("agent_shutdown", f"Agent '{agent_name}' shut down")
        return jsonify({"status": "success", "message": status_message}), 200
    return jsonify({"status": "error", "message": f"Agent {agent_name} not found."}), 404

# NEW: Dashboard API endpoints
@app.route("/api/agents")
def api_agents():
    return jsonify(hub_memory["active_agents"])

@app.route("/api/plugins", methods=["GET"])
def api_plugins():
    # Return all discovered plugins, along with their global status
    discovered_plugins_list = []
    for plugin_name, details in hub_memory["discovered_plugins"].items():
        plugin_status = hub_memory["global_plugin_preferences"].get(plugin_name, "enabled")
        details["status"] = plugin_status # Update status based on global preference
        discovered_plugins_list.append(details)
    return jsonify(discovered_plugins_list), 200

@app.route("/api/plugins/toggle/<plugin_name>", methods=["POST"])
def api_toggle_plugin(plugin_name):
    action = request.json.get("action") # "enable" or "disable"
    if action not in ["enable", "disable"]:
        return jsonify({"status": "error", "message": "Invalid action. Must be 'enable' or 'disable'."}), 400

    if plugin_name not in hub_memory["global_plugin_preferences"] and action == "disable":
        # If a plugin isn't explicitly listed, it means it's implicitly enabled.
        # So we can set its status to disabled if requested.
        hub_memory["global_plugin_preferences"][plugin_name] = "disabled"
        logger.info(f"Plugin '{plugin_name}' explicitly set to 'disabled'.")
        return jsonify({"status": "success", "message": f"Plugin '{plugin_name}' disabled. Agent restart required."}), 200
    elif plugin_name in hub_memory["global_plugin_preferences"]:
        hub_memory["global_plugin_preferences"][plugin_name] = action + "d" # "enabled" or "disabled"
        logger.info(f"Plugin '{plugin_name}' set to '{action}d'.")
        return jsonify({"status": "success", "message": f"Plugin '{plugin_name}' {action}d. Agent restart required."}), 200
    
    return jsonify({"status": "error", "message": f"Plugin '{plugin_name}' not found or no change."}), 404

@app.route("/api/agent/<agent_name>/plugin_status/<plugin_name>", methods=["GET"])
def api_agent_plugin_status(agent_name, plugin_name):
    # Agents query this to know if they should load a plugin
    status = hub_memory["global_plugin_preferences"].get(plugin_name, "enabled") # Default to enabled
    return jsonify({"status": status}), 200

# NEW: Plugin Upload Endpoint
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'temp_plugin_uploads')
ALLOWED_EXTENSIONS = {'zip'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/api/plugins/upload", methods=["POST"])
def api_upload_plugin():
    # Ensure the upload folder exists
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    if 'plugin_zip' not in request.files:
        return jsonify({"status": "error", "message": "No file part in the request."}), 400
    file = request.files['plugin_zip']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file."}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        logger.info(f"Received plugin upload: {filename}")

        try:
            with zipfile.ZipFile(filepath, 'r') as zip_ref:
                # Get the name of the top-level folder inside the zip
                namelist = zip_ref.namelist()
                if not namelist:
                    raise ValueError("Zip file is empty.")

                # Determine target directory name (e.g., from zip filename or manifest)
                plugin_base_name = os.path.splitext(filename)[0]
                extract_to_path = Path(__file__).parent / "plugins" / plugin_base_name

                # Zip-slip protection: validate all paths before extraction
                for member in namelist:
                    member_path = (extract_to_path / member).resolve()
                    if not str(member_path).startswith(str(extract_to_path.resolve())):
                        raise ValueError(f"Zip contains path traversal entry: {member}")

                # Check for existing plugin with the same name
                if extract_to_path.exists():
                    shutil.rmtree(extract_to_path) # Overwrite existing plugin if present
                    logger.warning(f"Existing plugin directory '{plugin_base_name}' overwritten.")

                os.makedirs(extract_to_path, exist_ok=True)
                zip_ref.extractall(extract_to_path)
            
            # Basic validation: check for manifest.json
            if not (extract_to_path / "manifest.json").is_file():
                shutil.rmtree(extract_to_path) # Clean up if invalid structure
                raise ValueError("Uploaded plugin is missing manifest.json.")

            logger.info(f"Plugin '{plugin_base_name}' extracted to {extract_to_path}. Agent restart required.")
            return jsonify({"status": "success", "message": f"Plugin '{plugin_base_name}' uploaded and extracted successfully. Restart your agents to activate."}), 200

        except zipfile.BadZipFile:
            logger.error(f"Uploaded file '{filename}' is not a valid zip file.")
            return jsonify({"status": "error", "message": "Uploaded file is not a valid zip file."}), 400
        except ValueError as ve:
            logger.error(f"Plugin validation failed: {ve}")
            return jsonify({"status": "error", "message": f"Plugin validation failed: {ve}"}), 400
        except Exception as e:
            logger.error(f"Error processing plugin upload: {e}")
            return jsonify({"status": "error", "message": f"Error processing plugin upload: {e}"}), 500
        finally:
            # Clean up the temporary zip file
            if os.path.exists(filepath):
                os.remove(filepath)
    else:
        return jsonify({"status": "error", "message": "Invalid file type. Only .zip files are allowed."}), 400

@app.route("/api/plugins/remove/<plugin_name>", methods=["POST"])
def api_remove_plugin(plugin_name):
    plugin_path = Path(__file__).parent / "plugins" / plugin_name
    if not plugin_path.is_dir():
        return jsonify({"status": "error", "message": f"Plugin '{plugin_name}' not found."}), 404
    
    try:
        shutil.rmtree(plugin_path)
        if plugin_name in hub_memory["global_plugin_preferences"]:
            del hub_memory["global_plugin_preferences"][plugin_name] # Remove preference entry
        logger.info(f"Plugin '{plugin_name}' removed successfully from {plugin_path}.")
        return jsonify({"status": "success", "message": f"Plugin '{plugin_name}' removed. Restart your agents to fully unregister its tools."}), 200
    except Exception as e:
        logger.error(f"Error removing plugin '{plugin_name}': {e}")
        return jsonify({"status": "error", "message": f"Failed to remove plugin '{plugin_name}': {e}"}), 500

@app.route("/api/experiences")
def api_experiences():
    return jsonify(hub_memory["recent_experiences"]) # Return recent experiences for dashboard

@app.route("/api/user_messages", methods=["GET", "POST"])
def api_user_messages():
    if request.method == "POST":
        data = request.json
        message = data.get("message")
        agent_name = data.get("agent_name")
        if message and agent_name:
            if agent_name not in hub_memory["agent_message_queues"]:
                hub_memory["agent_message_queues"][agent_name] = [] # Initialize if not exists
                logger.info(f"Created message queue for new agent '{agent_name}'.")
            hub_memory["agent_message_queues"][agent_name].append({"sender": "user", "message": message})
            logger.info(f"User message for agent '{agent_name}' queued.")
            return jsonify({"status": "success", "message": "Message queued for processing."}), 200
        return jsonify({"status": "error", "message": "Invalid message or agent_name."}), 400
    else: # GET
        return jsonify(hub_memory["user_messages"]) # Return messages from agents to user

@app.route("/api/agent_message_queue/<agent_name>", methods=["GET"])
def api_agent_message_queue(agent_name):
    if agent_name in hub_memory["agent_message_queues"]:
        messages = hub_memory["agent_message_queues"][agent_name]
        hub_memory["agent_message_queues"][agent_name] = [] # Clear queue after retrieval
        return jsonify(messages), 200
    return jsonify({"status": "error", "message": f"Agent {agent_name} not found or no message queue."}), 404

# NEW: Approval API endpoints
@app.route("/api/request_approval", methods=["POST"])
def request_approval():
    data = request.json
    agent_name = data.get("agent_name")
    tool_name = data.get("tool_name")
    tool_args = data.get("tool_args")

    if not all([agent_name, tool_name, tool_args]):
        return jsonify({"status": "error", "message": "Missing required data for approval request."}), 400
    
    request_id = str(uuid.uuid4())
    hub_memory["pending_approvals"][request_id] = {
        "agent_name": agent_name,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "status": "pending", # pending, approved, denied
        "timestamp": time.time()
    }
    logger.info(f"Approval request '{request_id}' from agent '{agent_name}' for tool '{tool_name}' logged.")
    _add_notification("approval_needed", f"Agent '{agent_name}' needs approval for '{tool_name}'")
    return jsonify({"status": "success", "request_id": request_id}), 200

@app.route("/api/approvals", methods=["GET"])
def get_approvals():
    return jsonify(hub_memory["pending_approvals"]), 200

@app.route("/api/token_usage", methods=["GET"])
def get_token_usage():
    return jsonify({
        "global": hub_memory["total_token_usage"],
        "per_agent": hub_memory["per_agent_token_usage"]
    }), 200

@app.route("/api/approve/<request_id>", methods=["POST"])
def approve_request(request_id):
    if request_id not in hub_memory["pending_approvals"]:
        return jsonify({"status": "error", "message": "Request ID not found."}), 404

    decision = request.json.get("decision") # "approved" or "denied"
    if decision not in ["approved", "denied"]:
        return jsonify({"status": "error", "message": "Invalid decision. Must be 'approved' or 'denied'."}), 400
    
    hub_memory["pending_approvals"][request_id]["status"] = decision
    logger.info(f"Approval request '{request_id}' has been '{decision}'.")
    return jsonify({"status": "success", "message": f"Request {request_id} has been {decision}."}), 200

@app.route("/api/check_approval/<request_id>", methods=["GET"])
def check_approval(request_id):
    if request_id not in hub_memory["pending_approvals"]:
        return jsonify({"status": "error", "message": "Request ID not found."}), 404
    
    approval_status = hub_memory["pending_approvals"][request_id]["status"]
    if approval_status in ["approved", "denied"]:
        # Once an agent has checked the final status, we can remove it from the list
        # to prevent it from growing indefinitely.
        del hub_memory["pending_approvals"][request_id]
        logger.info(f"Agent checked final status of request '{request_id}'. Removing from pending list.")
    
    return jsonify({"status": approval_status}), 200

# NEW: Configuration API endpoints
DOTENV_PATH = os.path.join(os.path.dirname(__file__), '.env')
if not os.path.exists(DOTENV_PATH):
    # If .env is not in the current directory, try one level up (project root)
    DOTENV_PATH = os.path.join(os.path.dirname(__file__), '..', '.env')

@app.route("/api/config", methods=["GET"])
def api_get_config():
    load_dotenv(DOTENV_PATH) # Reload to get latest values

    # List of all config keys that should be read from environment
    all_keys = [
        "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OLLAMA_BASE_URL", "OLLAMA_MODEL",
        "MOONSHOT_API_KEY", "MOONSHOT_MODEL", "HUGGINGFACE_API_KEY", "HUGGINGFACE_MODEL",
        "DISCORDAGENT_DISCORD_TOKEN",
        "DISCORDAGENT_DISCORD_CHANNEL_ID", "TELEGRAMAGENT_TELEGRAM_TOKEN",
        "TELEGRAMAGENT_TELEGRAM_ALLOWED_CHATS", "LINEAGENT_LINE_CHANNEL_SECRET",
        "LINEAGENT_LINE_CHANNEL_ACCESS_TOKEN", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
        "X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
        "EXA_API_KEY",
        "ENABLE_MOONSHOT_AI", "ENABLE_OLLAMA", "ENABLE_OPENROUTER", "ENABLE_HUGGINGFACE",
        "VOYAGE_AI_API_KEY",
        "ENABLE_VOYAGE_AI", "ENABLE_VOICE_TOOLS", "EMAIL_API_SERVICE", "EMAIL_API_AUTH_METHOD",
        "CALENDAR_API_SERVICE", "CALENDAR_API_AUTH_METHOD", "DEFAULT_PROACTIVE_LOOPS",
        "DEFAULT_EXECUTION_MODE", "DEFAULT_BATCH_EXPERIENCE", "DEFAULT_PROACTIVE_INTERVAL",
        "SCHEDULER_TIMEZONE"
    ]
    
    # List of keys that are sensitive and should be masked
    sensitive_keys = [
        "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "HUGGINGFACE_API_KEY",
        "DISCORDAGENT_DISCORD_TOKEN",
        "TELEGRAMAGENT_TELEGRAM_TOKEN", "LINEAGENT_LINE_CHANNEL_SECRET",
        "LINEAGENT_LINE_CHANNEL_ACCESS_TOKEN", "TWILIO_AUTH_TOKEN",
        "WHATSAPP_ACCESS_TOKEN", "X_CONSUMER_KEY", "X_CONSUMER_SECRET",
        "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "VOYAGE_AI_API_KEY",
        "EXA_API_KEY"
    ]

    config = {key: os.environ.get(key, "") for key in all_keys}

    # Mask sensitive keys for security
    # If a key exists, send an empty string to the frontend for display
    for key in sensitive_keys:
        logger.debug(f"[DEBUG api_get_config] Before masking - Key: {key}, Value: '{config[key]}'")
        if config[key]:
            config[key] = "" # Mask by sending empty string
            logger.debug(f"[DEBUG api_get_config] After masking - Key: {key}, Value: '{config[key]}'")
    return jsonify(config), 200

@app.route("/api/config", methods=["POST"])
def api_set_config():
    from dotenv import set_key
    
    # Reload .env at the start to get the absolute latest environment variables
    load_dotenv(DOTENV_PATH) 

    data = request.json
    
    # Helper to safely update sensitive keys, preventing empty values from overwriting real ones
    def update_sensitive_key(key_name, incoming_value):
        current_value_in_env = os.environ.get(key_name, "")
        logger.debug(f"[DEBUG api_set_config] Key: {key_name}")
        logger.debug(f"[DEBUG api_set_config] Incoming value: '{incoming_value}'")
        logger.debug(f"[DEBUG api_set_config] Current value in env (pre-load_dotenv): '{current_value_in_env}'")
        if incoming_value == "" and current_value_in_env:
            logger.info(f"[DEBUG api_set_config] {key_name} received as empty value. Retaining existing key in .env.")
        else:
            logger.debug(f"[DEBUG api_set_config] Setting {key_name} to '{incoming_value}'.")
            set_key(DOTENV_PATH, key_name, incoming_value)

    # Process all keys from the form
    sensitive_keys = [
        "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "HUGGINGFACE_API_KEY",
        "DISCORDAGENT_DISCORD_TOKEN",
        "TELEGRAMAGENT_TELEGRAM_TOKEN", "LINEAGENT_LINE_CHANNEL_SECRET",
        "LINEAGENT_LINE_CHANNEL_ACCESS_TOKEN", "TWILIO_AUTH_TOKEN",
        "WHATSAPP_ACCESS_TOKEN", "X_CONSUMER_KEY", "X_CONSUMER_SECRET",
        "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "VOYAGE_AI_API_KEY",
        "EXA_API_KEY"
    ]
    
    checkbox_keys = ["ENABLE_MOONSHOT_AI", "ENABLE_OLLAMA", "ENABLE_OPENROUTER", "ENABLE_HUGGINGFACE", "ENABLE_VOYAGE_AI", "ENABLE_VOICE_TOOLS"]

    for key, value in data.items():
        if key in sensitive_keys:
            update_sensitive_key(key, value)
        elif key in checkbox_keys:
            logger.debug(f"[DEBUG api_set_config] Checkbox {key} is checked. Setting to 'yes'.")
            set_key(DOTENV_PATH, key, "yes") # If key is present, it's checked
        else:
            logger.debug(f"[DEBUG api_set_config] Setting non-sensitive key {key} to '{value}'.")
            set_key(DOTENV_PATH, key, value)
    
    # Handle unchecked checkboxes
    for key in checkbox_keys:
        if key not in data:
            logger.debug(f"[DEBUG api_set_config] Checkbox {key} is unchecked. Setting to 'no'.")
            set_key(DOTENV_PATH, key, "no")

    # After modifying .env, reload all environment variables into os.environ for the current process
    load_dotenv(DOTENV_PATH, override=True)

    logger.info("Configuration updated via dashboard API.")
    return jsonify({"status": "success", "message": "Configuration updated."}), 200

# NEW: API endpoint for auto-install dependencies preference
@app.route("/api/settings/auto_install_deps", methods=["GET", "POST"])
def api_auto_install_deps_setting():
    if request.method == "GET":
        return jsonify({"status": hub_memory["global_auto_install_deps"]}), 200
    elif request.method == "POST":
        data = request.json
        enable = data.get("enable")
        if isinstance(enable, bool):
            hub_memory["global_auto_install_deps"] = enable
            logger.info(f"Global auto-install dependencies set to: {enable}")
            return jsonify({"status": "success", "message": f"Auto-install dependencies set to {enable}."}), 200
        return jsonify({"status": "error", "message": "Invalid 'enable' value. Must be boolean."}), 400

# Cache for OpenRouter models list
_openrouter_models_cache = {"data": None, "timestamp": 0}
OPENROUTER_CACHE_TTL = 600  # 10 minutes

@app.route("/api/openrouter/models", methods=["GET"])
def api_openrouter_models():
    """Fetch available models from OpenRouter API with free/paid info."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return jsonify({"status": "error", "message": "OPENROUTER_API_KEY not configured."}), 400

    include_paid = request.args.get("include_paid", "false").lower() == "true"

    # Check cache
    now = time.time()
    if _openrouter_models_cache["data"] and (now - _openrouter_models_cache["timestamp"]) < OPENROUTER_CACHE_TTL:
        all_models = _openrouter_models_cache["data"]
    else:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=15)
            resp.raise_for_status()
            raw_models = resp.json().get("data", [])

            all_models = []
            for m in raw_models:
                prompt_price = m.get("pricing", {}).get("prompt", "0")
                is_free = str(prompt_price) == "0"
                all_models.append({
                    "id": m.get("id", ""),
                    "name": m.get("name", m.get("id", "")),
                    "free": is_free,
                    "context_length": m.get("context_length", 0),
                })
            # Sort: free first, then alphabetically
            all_models.sort(key=lambda x: (not x["free"], x["name"].lower()))

            _openrouter_models_cache["data"] = all_models
            _openrouter_models_cache["timestamp"] = now
            logger.info(f"Fetched {len(all_models)} models from OpenRouter ({sum(1 for m in all_models if m['free'])} free).")
        except Exception as e:
            logger.error(f"Error fetching OpenRouter models: {e}")
            return jsonify({"status": "error", "message": f"Failed to fetch models: {e}"}), 500

    if include_paid:
        filtered = all_models
    else:
        filtered = [m for m in all_models if m["free"]]

    return jsonify({"status": "success", "models": filtered}), 200

# Cache for HuggingFace models list
_huggingface_models_cache = {"data": None, "timestamp": 0}
HUGGINGFACE_CACHE_TTL = 600  # 10 minutes

@app.route("/api/huggingface/models", methods=["GET"])
def api_huggingface_models():
    """Fetch available text-generation models from HuggingFace Inference API."""
    api_key = os.environ.get("HUGGINGFACE_API_KEY")
    if not api_key:
        return jsonify({"status": "error", "message": "HUGGINGFACE_API_KEY not configured."}), 400

    # Check cache
    now = time.time()
    if _huggingface_models_cache["data"] and (now - _huggingface_models_cache["timestamp"]) < HUGGINGFACE_CACHE_TTL:
        all_models = _huggingface_models_cache["data"]
    else:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get(
                "https://api-inference.huggingface.co/v1/models",
                headers=headers,
                timeout=15
            )
            resp.raise_for_status()
            raw_models = resp.json()

            all_models = []
            if isinstance(raw_models, list):
                for m in raw_models:
                    model_id = m.get("id", "") if isinstance(m, dict) else str(m)
                    pipeline = m.get("pipeline_tag", "") if isinstance(m, dict) else ""
                    if pipeline and pipeline != "text-generation":
                        continue
                    all_models.append({
                        "id": model_id,
                        "name": model_id,
                        "pipeline": pipeline,
                    })
            all_models.sort(key=lambda x: x["name"].lower())

            _huggingface_models_cache["data"] = all_models
            _huggingface_models_cache["timestamp"] = now
            logger.info(f"Fetched {len(all_models)} models from HuggingFace.")
        except Exception as e:
            logger.error(f"Error fetching HuggingFace models: {e}")
            return jsonify({"status": "error", "message": f"Failed to fetch models: {e}"}), 500

    return jsonify({"status": "success", "models": all_models}), 200

@app.route("/send_message_to_agent_by_name", methods=["POST"])
def send_message_to_agent_by_name():
    data = request.json
    target_agent_name = data.get("target_agent_name")
    message = data.get("message")
    sender_agent_name = data.get("sender_agent_name")
    if target_agent_name and message:
        if target_agent_name not in hub_memory["agent_message_queues"]:
            hub_memory["agent_message_queues"][target_agent_name] = []
        hub_memory["agent_message_queues"][target_agent_name].append({
            "sender": sender_agent_name or "unknown_agent",
            "message": message
        })
        logger.info(f"Message from agent '{sender_agent_name}' queued for agent '{target_agent_name}'.")
        return jsonify({"status": "success", "message": f"Message queued for agent '{target_agent_name}'."}), 200
    return jsonify({"status": "error", "message": "Invalid target_agent_name or message."}), 400

@app.route("/receive_user_message", methods=["POST"])
def receive_user_message():
    data = request.json
    agent_name = data.get("agent_name")
    message = data.get("message")
    title = data.get("title")
    if agent_name and message:
        hub_memory["user_messages"].append({
            "agent_name": agent_name,
            "message": message,
            "title": title,
            "timestamp": time.time()
        })
        logger.info(f"Received message from agent '{agent_name}' for user.")
        # Check if this completes a workflow step
        _check_workflow_step_completion(agent_name, message)
        return jsonify({"status": "success", "message": "Message queued for processing."}), 200
    return jsonify({"status": "error", "message": "Invalid message data."}), 400

@app.route("/shutdown", methods=["POST"])
def shutdown_hub():
    logger.info("Hub shutdown requested.")
    # Use a separate thread to shut down the server to allow the response to be sent
    threading.Thread(target=initiate_shutdown).start()
    return jsonify({"status": "success", "message": "Hub is shutting down."}), 200

def initiate_shutdown():
    global waitress_server
    if waitress_server:
        logger.info("Attempting to stop Waitress server...")
        waitress_server.shutdown() # This is the graceful way to stop Waitress
        logger.info("Waitress server stopped.")
    # Terminate all launched agent processes
    for agent_name, pid in list(launched_agent_processes.items()): # Iterate over a copy
        try:
            os.killpg(pid, signal.SIGTERM) # Use SIGTERM
            logger.info(f"Terminated agent process group for '{agent_name}' (PID: {pid}) during hub shutdown.")
        except ProcessLookupError:
            logger.warning(f"Agent process for '{agent_name}' (PID: {pid}) not found during hub shutdown, already terminated.")
        except Exception as e:
            logger.error(f"Error terminating agent process group for '{agent_name}' (PID: {pid}) during hub shutdown: {e}")
    launched_agent_processes.clear() # Clear the dictionary after attempting to kill all

    shutdown_event.set() # Signal main thread to exit


@app.route("/api/reset", methods=["POST"])
def reset_hub():
    """Reset the hub: kill all agents and clear all in-memory state."""
    logger.info("Hub reset requested.")

    # Kill all launched agent processes
    killed = 0
    for agent_name, pid in list(launched_agent_processes.items()):
        try:
            os.killpg(pid, signal.SIGTERM)
            logger.info(f"Reset: Terminated agent '{agent_name}' (PID: {pid})")
            killed += 1
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.error(f"Reset: Error killing agent '{agent_name}' (PID: {pid}): {e}")
    launched_agent_processes.clear()

    # Clear all in-memory state (preserve constants and config)
    hub_memory["experiences"] = []
    hub_memory["distilled_tips"] = []
    hub_memory["active_agents"] = {}
    hub_memory["agent_message_queues"] = {}
    hub_memory["recent_experiences"] = []
    hub_memory["user_messages"] = []
    hub_memory["pending_approvals"] = {}
    hub_memory["total_token_usage"] = {
        "moonshot_ai": {"prompt_tokens": 0, "completion_tokens": 0},
        "ollama": {"prompt_tokens": 0, "completion_tokens": 0},
        "openrouter": {"prompt_tokens": 0, "completion_tokens": 0},
        "voyage_ai": {"prompt_tokens": 0, "completion_tokens": 0},
        "huggingface": {"prompt_tokens": 0, "completion_tokens": 0}
    }
    hub_memory["per_agent_token_usage"] = {}
    hub_memory["notifications"] = []
    _save_notifications()
    hub_memory["workflow_runs"] = []
    _save_workflow_runs()
    # Keep global_plugin_preferences, global_auto_install_deps, discovered_plugins, scheduled_tasks, webhooks, agent_templates, workflows

    logger.info(f"Hub reset complete. Killed {killed} agents, cleared all memory.")
    return jsonify({"status": "success", "message": f"Hub reset. Killed {killed} agent(s), cleared all data."}), 200


@app.route("/api/timezones", methods=["GET"])
def api_timezones():
    """Return list of common timezones for the dashboard dropdown."""
    return jsonify({"status": "success", "timezones": pytz.common_timezones}), 200


# ─── Scheduled Tasks API ───

@app.route("/api/scheduled_tasks", methods=["GET"])
def api_get_scheduled_tasks():
    """List all scheduled tasks with next run time."""
    tasks = []
    for t in hub_memory["scheduled_tasks"]:
        task_copy = dict(t)
        # Get next run time from APScheduler
        try:
            job = _scheduler.get_job(t["id"])
            if job and job.next_run_time:
                task_copy["next_run"] = job.next_run_time.isoformat()
            else:
                task_copy["next_run"] = None
        except Exception:
            task_copy["next_run"] = None
        tasks.append(task_copy)
    return jsonify({"status": "success", "tasks": tasks}), 200

@app.route("/api/scheduled_tasks", methods=["POST"])
def api_create_scheduled_task():
    """Create a new scheduled task."""
    data = request.json
    agent_name = data.get("agent_name")
    task_message = data.get("task_message")
    schedule_type = data.get("schedule_type", "interval")

    if not agent_name or not task_message:
        return jsonify({"status": "error", "message": "agent_name and task_message are required."}), 400

    task_id = str(uuid.uuid4())[:8]

    task_record = {
        "id": task_id,
        "agent_name": agent_name,
        "task_message": task_message,
        "schedule_type": schedule_type,
        "enabled": True,
        "created_at": time.time(),
        "last_run": None
    }

    if schedule_type == "cron":
        task_record["cron_hour"] = data.get("cron_hour", 9)
        task_record["cron_minute"] = data.get("cron_minute", 0)
        task_record["cron_days"] = data.get("cron_days", "*")
    else:
        task_record["interval_seconds"] = int(data.get("interval_hours", 6)) * 3600

    hub_memory["scheduled_tasks"].append(task_record)
    _register_task_with_scheduler(task_record)
    _save_scheduled_tasks()

    logger.info(f"[Scheduler] Created task '{task_id}' for agent '{agent_name}': {task_message[:80]}")
    return jsonify({"status": "success", "task": task_record}), 200

@app.route("/api/scheduled_tasks/<task_id>", methods=["DELETE"])
def api_delete_scheduled_task(task_id):
    """Delete a scheduled task."""
    try:
        _scheduler.remove_job(task_id)
    except Exception:
        pass  # Job may not exist in scheduler
    hub_memory["scheduled_tasks"] = [t for t in hub_memory["scheduled_tasks"] if t["id"] != task_id]
    _save_scheduled_tasks()
    logger.info(f"[Scheduler] Deleted task '{task_id}'.")
    return jsonify({"status": "success"}), 200

@app.route("/api/scheduled_tasks/<task_id>/toggle", methods=["POST"])
def api_toggle_scheduled_task(task_id):
    """Enable or disable a scheduled task."""
    for t in hub_memory["scheduled_tasks"]:
        if t["id"] == task_id:
            t["enabled"] = not t.get("enabled", True)
            if t["enabled"]:
                _register_task_with_scheduler(t)
            else:
                try:
                    _scheduler.remove_job(task_id)
                except Exception:
                    pass
            _save_scheduled_tasks()
            return jsonify({"status": "success", "enabled": t["enabled"]}), 200
    return jsonify({"status": "error", "message": "Task not found."}), 404


# ─── Webhook API ───

@app.route("/api/webhooks", methods=["GET"])
def api_get_webhooks():
    """List all webhooks."""
    return jsonify({"status": "success", "webhooks": list(hub_memory["webhooks"].values())}), 200

@app.route("/api/webhooks", methods=["POST"])
def api_create_webhook():
    """Create a new webhook."""
    data = request.json
    name = data.get("name", "").strip()
    agent_name = data.get("agent_name", "").strip()

    if not name or not agent_name:
        return jsonify({"status": "error", "message": "name and agent_name are required."}), 400

    webhook_id = str(uuid.uuid4())[:8]
    secret = secrets.token_urlsafe(16)

    rate_limit = int(data.get("rate_limit", 0))
    condition = data.get("condition", "").strip()

    webhook = {
        "id": webhook_id,
        "name": name,
        "agent_name": agent_name,
        "secret": secret,
        "created_at": time.time(),
        "enabled": True,
        "last_triggered": None,
        "trigger_count": 0,
        "rate_limit": max(rate_limit, 0),
        "rate_window": [],
        "condition": condition
    }
    hub_memory["webhooks"][webhook_id] = webhook
    _save_webhooks()

    logger.info(f"[Webhooks] Created webhook '{name}' (id={webhook_id}) for agent '{agent_name}'")
    return jsonify({"status": "success", "webhook": webhook}), 200

@app.route("/api/webhooks/<webhook_id>", methods=["DELETE"])
def api_delete_webhook(webhook_id):
    """Delete a webhook."""
    if webhook_id in hub_memory["webhooks"]:
        del hub_memory["webhooks"][webhook_id]
        _save_webhooks()
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "error", "message": "Webhook not found."}), 404

@app.route("/api/webhooks/<webhook_id>/toggle", methods=["POST"])
def api_toggle_webhook(webhook_id):
    """Enable or disable a webhook."""
    if webhook_id in hub_memory["webhooks"]:
        hub_memory["webhooks"][webhook_id]["enabled"] = not hub_memory["webhooks"][webhook_id]["enabled"]
        _save_webhooks()
        return jsonify({"status": "success", "enabled": hub_memory["webhooks"][webhook_id]["enabled"]}), 200
    return jsonify({"status": "error", "message": "Webhook not found."}), 404

@app.route("/webhook/<webhook_id>", methods=["POST"])
def webhook_trigger(webhook_id):
    """External trigger endpoint — fires a message into an agent's queue."""
    if webhook_id not in hub_memory["webhooks"]:
        return jsonify({"status": "error", "message": "Webhook not found."}), 404

    wh = hub_memory["webhooks"][webhook_id]

    if not wh["enabled"]:
        return jsonify({"status": "error", "message": "Webhook is disabled."}), 403

    # Validate secret (query param or header)
    provided_secret = request.args.get("secret") or request.headers.get("X-Webhook-Secret", "")
    if provided_secret != wh["secret"]:
        return jsonify({"status": "error", "message": "Invalid secret."}), 401

    # Rate limiting
    rate_limit = wh.get("rate_limit", 0)
    if rate_limit and rate_limit > 0:
        now = time.time()
        window = wh.get("rate_window", [])
        # Keep only timestamps within the last 60 seconds
        window = [t for t in window if now - t < 60]
        if len(window) >= rate_limit:
            logger.warning(f"[Webhooks] Rate limit exceeded for webhook '{wh['name']}' ({rate_limit}/min)")
            return jsonify({"status": "error", "message": f"Rate limit exceeded ({rate_limit} calls/min)."}), 429
        window.append(now)
        wh["rate_window"] = window

    # Extract message from body
    body = request.get_json(silent=True) or {}
    message = body.get("message", "")
    if not message:
        # If no message field, use the entire payload as context
        message = f"Webhook '{wh['name']}' triggered with payload: {json.dumps(body)}"

    # Condition filter — only proceed if payload contains the keyword (case-insensitive)
    condition = wh.get("condition", "").strip()
    if condition:
        payload_text = json.dumps(body).lower() + " " + message.lower()
        if condition.lower() not in payload_text:
            logger.info(f"[Webhooks] Webhook '{wh['name']}' skipped — condition '{condition}' not matched in payload")
            return jsonify({"status": "skipped", "message": f"Condition not met: '{condition}' not found in payload."}), 200

    agent_name = wh["agent_name"]
    if agent_name not in hub_memory["agent_message_queues"]:
        hub_memory["agent_message_queues"][agent_name] = []
    hub_memory["agent_message_queues"][agent_name].append({
        "sender": "webhook",
        "message": message
    })

    wh["last_triggered"] = time.time()
    wh["trigger_count"] += 1
    _save_webhooks()

    _add_notification("webhook_triggered", f"Webhook '{wh['name']}' triggered for agent '{agent_name}'")
    logger.info(f"[Webhooks] Webhook '{wh['name']}' (id={webhook_id}) triggered for agent '{agent_name}'")
    return jsonify({"status": "success", "message": f"Message sent to agent '{agent_name}'."}), 200


# ─── Agent Groups API ───

@app.route("/api/agent_groups", methods=["GET"])
def api_list_agent_groups():
    return jsonify({"status": "success", "groups": list(hub_memory["agent_groups"].values())}), 200

@app.route("/api/agent_groups", methods=["POST"])
def api_create_agent_group():
    data = request.json or {}
    name = data.get("name", "").strip()
    agents = data.get("agents", [])
    if not name:
        return jsonify({"status": "error", "message": "Group name is required."}), 400
    if name in hub_memory["agent_groups"]:
        return jsonify({"status": "error", "message": f"Group '{name}' already exists."}), 400
    hub_memory["agent_groups"][name] = {
        "name": name,
        "agents": [a.strip() for a in agents if a.strip()],
        "created_at": time.time()
    }
    _save_agent_groups()
    logger.info(f"[Groups] Created group '{name}' with agents: {agents}")
    return jsonify({"status": "success", "group": hub_memory["agent_groups"][name]}), 200

@app.route("/api/agent_groups/<group_name>", methods=["PUT"])
def api_update_agent_group(group_name):
    if group_name not in hub_memory["agent_groups"]:
        return jsonify({"status": "error", "message": "Group not found."}), 404
    data = request.json or {}
    agents = data.get("agents", [])
    hub_memory["agent_groups"][group_name]["agents"] = [a.strip() for a in agents if a.strip()]
    _save_agent_groups()
    return jsonify({"status": "success", "group": hub_memory["agent_groups"][group_name]}), 200

@app.route("/api/agent_groups/<group_name>", methods=["DELETE"])
def api_delete_agent_group(group_name):
    if group_name in hub_memory["agent_groups"]:
        del hub_memory["agent_groups"][group_name]
        _save_agent_groups()
        return jsonify({"status": "success"}), 200
    return jsonify({"status": "error", "message": "Group not found."}), 404

@app.route("/api/agent_groups/<group_name>/send", methods=["POST"])
def api_send_to_group(group_name):
    """Send a message to all agents in a group."""
    if group_name not in hub_memory["agent_groups"]:
        return jsonify({"status": "error", "message": "Group not found."}), 404
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"status": "error", "message": "Message is required."}), 400
    group = hub_memory["agent_groups"][group_name]
    sent_to = []
    for agent_name in group["agents"]:
        if agent_name in hub_memory["active_agents"]:
            if agent_name not in hub_memory["agent_message_queues"]:
                hub_memory["agent_message_queues"][agent_name] = []
            hub_memory["agent_message_queues"][agent_name].append({
                "sender": f"group:{group_name}",
                "message": message
            })
            sent_to.append(agent_name)
    _add_notification("group_message", f"Message sent to group '{group_name}' ({len(sent_to)} agents)")
    logger.info(f"[Groups] Message sent to group '{group_name}': {sent_to}")
    return jsonify({"status": "success", "sent_to": sent_to, "message": f"Sent to {len(sent_to)} agent(s)."}), 200


# ─── Notification API ───

@app.route("/api/notifications", methods=["GET"])
def api_get_notifications():
    """Return the notification feed (most recent first)."""
    return jsonify({"status": "success", "notifications": hub_memory["notifications"]}), 200

@app.route("/api/notifications/read", methods=["POST"])
def api_mark_notifications_read():
    """Mark all notifications as read."""
    for n in hub_memory["notifications"]:
        n["read"] = True
    _save_notifications()
    return jsonify({"status": "success"}), 200


# ─── Broadcast API ───

@app.route("/api/broadcast", methods=["POST"])
def api_broadcast():
    """Send a message to ALL active agents."""
    data = request.json
    message = data.get("message", "").strip()
    sender = data.get("sender", "broadcast")

    if not message:
        return jsonify({"status": "error", "message": "Message is required."}), 400

    count = 0
    for agent_name in hub_memory["active_agents"]:
        if agent_name not in hub_memory["agent_message_queues"]:
            hub_memory["agent_message_queues"][agent_name] = []
        hub_memory["agent_message_queues"][agent_name].append({
            "sender": sender,
            "message": message
        })
        count += 1

    logger.info(f"[Broadcast] Sent message to {count} agent(s): {message[:80]}")
    _add_notification("broadcast", f"Broadcast sent to {count} agent(s): {message[:60]}")
    return jsonify({"status": "success", "message": f"Broadcast sent to {count} agent(s)."}), 200


# ─── Agent Templates API ───

@app.route("/api/agent_templates", methods=["GET"])
def api_get_agent_templates():
    """List all saved agent templates."""
    return jsonify({"status": "success", "templates": hub_memory["agent_templates"]}), 200

@app.route("/api/agent_templates", methods=["POST"])
def api_create_agent_template():
    """Save an agent launch configuration as a reusable template."""
    data = request.json
    template_name = data.get("template_name", "").strip()

    if not template_name:
        return jsonify({"status": "error", "message": "template_name is required."}), 400

    template = {
        "id": str(uuid.uuid4())[:8],
        "template_name": template_name,
        "agent_name": data.get("agent_name", ""),
        "connector_type": data.get("connector_type", "none"),
        "is_proactive": data.get("is_proactive", False),
        "execution_mode": data.get("execution_mode", "safe"),
        "batch_experience": data.get("batch_experience", False),
        "proactive_interval": data.get("proactive_interval", 600),
        "llm_provider": data.get("llm_provider", ""),
        "llm_model": data.get("llm_model", ""),
        "initial_goal": data.get("initial_goal", ""),
        "created_at": time.time()
    }
    hub_memory["agent_templates"].append(template)
    _save_agent_templates()

    logger.info(f"[Templates] Saved template '{template_name}' (id={template['id']})")
    return jsonify({"status": "success", "template": template}), 200

@app.route("/api/agent_templates/<template_id>", methods=["DELETE"])
def api_delete_agent_template(template_id):
    """Delete an agent template."""
    hub_memory["agent_templates"] = [t for t in hub_memory["agent_templates"] if t["id"] != template_id]
    _save_agent_templates()
    return jsonify({"status": "success"}), 200


# ─── Agent Memory Viewer ───

@app.route("/api/agent/<agent_name>/memories", methods=["GET"])
def api_agent_memories(agent_name):
    """Return experiences stored in hub_memory for a given agent."""
    agent_experiences = [
        exp for exp in hub_memory["experiences"]
        if exp.get("agent_name") == agent_name
    ]
    # Return most recent first, limit to 50
    agent_experiences = list(reversed(agent_experiences[-50:]))
    return jsonify({"status": "success", "memories": agent_experiences}), 200


# ─── Agent Log Viewer ───

@app.route("/api/agent/<agent_name>/logs", methods=["GET"])
def api_agent_logs(agent_name):
    """Return the last N lines of an agent's stdout and stderr logs."""
    max_lines = int(request.args.get("lines", 100))
    base_dir = os.path.dirname(__file__)

    def _read_tail(filepath, n):
        try:
            with open(filepath, "r") as f:
                lines = f.readlines()
                return lines[-n:]
        except FileNotFoundError:
            return []
        except Exception as e:
            return [f"Error reading log: {e}"]

    stdout_path = os.path.join(base_dir, f"agent_{agent_name}_stdout.log")
    stderr_path = os.path.join(base_dir, f"agent_{agent_name}_stderr.log")

    return jsonify({
        "status": "success",
        "stdout": _read_tail(stdout_path, max_lines),
        "stderr": _read_tail(stderr_path, max_lines)
    }), 200


# ─── Agent Health Tracking ───

@app.route("/api/agent_health", methods=["GET"])
def api_agent_health():
    """Return per-agent health stats: uptime, tasks completed, errors, tokens."""
    health = {}
    now = time.time()
    for agent_name, details in hub_memory["active_agents"].items():
        # Count tasks and errors from experiences
        agent_exps = [e for e in hub_memory["experiences"] if e.get("agent_name") == agent_name]
        tasks_completed = sum(
            1 for e in agent_exps
            if e.get("step_result", {}).get("status") not in ("failed", "denied", None)
        )
        errors = sum(
            1 for e in agent_exps
            if e.get("step_result", {}).get("status") == "failed"
        )
        # Uptime from first heartbeat
        registered_at = details.get("registered_at", details.get("last_heartbeat", now))
        uptime_seconds = int(now - registered_at)
        # Last heartbeat age
        last_hb = details.get("last_heartbeat", 0)
        hb_age = int(now - last_hb) if last_hb else None
        # Token usage for this agent
        agent_tokens = hub_memory["per_agent_token_usage"].get(agent_name, {})
        total_tokens = sum(v.get("prompt_tokens", 0) + v.get("completion_tokens", 0) for v in agent_tokens.values())

        health[agent_name] = {
            "uptime_seconds": uptime_seconds,
            "tasks_completed": tasks_completed,
            "errors": errors,
            "last_heartbeat_age": hb_age,
            "total_tokens": total_tokens,
            "url": details.get("url", "")
        }
    return jsonify({"status": "success", "health": health}), 200


# ─── Cost Estimator ───

# Approximate pricing per 1M tokens (prompt / completion) — update as needed
_PROVIDER_PRICING = {
    "openrouter": {"prompt": 0.0, "completion": 0.0},  # Varies by model, free models = $0
    "huggingface": {"prompt": 0.0, "completion": 0.0},  # Free tier
    "ollama": {"prompt": 0.0, "completion": 0.0},       # Self-hosted, no cost
    "moonshot_ai": {"prompt": 1.0, "completion": 1.0},  # ~$1/M tokens
    "voyage_ai": {"prompt": 0.1, "completion": 0.0},    # Embeddings only
}

@app.route("/api/token_costs", methods=["GET"])
def api_token_costs():
    """Return estimated costs based on token usage and provider pricing."""
    costs = {}
    total_cost = 0.0
    for provider, usage in hub_memory["total_token_usage"].items():
        pricing = _PROVIDER_PRICING.get(provider, {"prompt": 0.0, "completion": 0.0})
        prompt_cost = (usage["prompt_tokens"] / 1_000_000) * pricing["prompt"]
        completion_cost = (usage["completion_tokens"] / 1_000_000) * pricing["completion"]
        provider_cost = prompt_cost + completion_cost
        costs[provider] = {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "prompt_cost": round(prompt_cost, 6),
            "completion_cost": round(completion_cost, 6),
            "total_cost": round(provider_cost, 6)
        }
        total_cost += provider_cost
    return jsonify({
        "status": "success",
        "costs": costs,
        "total_cost": round(total_cost, 6),
        "pricing": _PROVIDER_PRICING
    }), 200


# ─── Software Update System ───

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))  # agent_network/ (where .git lives)

def _get_current_version():
    """Read __version__ from __init__.py at import time and after updates."""
    try:
        init_path = os.path.join(_REPO_DIR, "__init__.py")
        with open(init_path, "r") as f:
            for line in f:
                if line.startswith("__version__"):
                    return line.split("=")[1].strip().strip("'\"")
    except Exception:
        pass
    return "unknown"

def _git(args, timeout=30):
    """Run a git command in the repo directory and return (success, stdout, stderr)."""
    try:
        result = _subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=timeout, cwd=_REPO_DIR
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)

@app.route("/api/version", methods=["GET"])
def api_version():
    """Check current version and whether updates are available."""
    version = _get_current_version()

    # Get current commit
    ok, current_hash, _ = _git(["rev-parse", "--short", "HEAD"])
    current_commit = current_hash if ok else "unknown"

    # Get current branch
    ok, branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch if ok else "develop"

    # Fetch latest from remote (non-destructive)
    _git(["fetch", "origin"], timeout=15)

    # Count commits behind
    ok, log_output, _ = _git(["log", f"HEAD..origin/{branch}", "--oneline"])
    commits = [line for line in log_output.split("\n") if line.strip()] if ok and log_output else []
    commits_behind = len(commits)

    # Get latest remote commit hash
    ok, latest_hash, _ = _git(["rev-parse", "--short", f"origin/{branch}"])
    latest_commit = latest_hash if ok else "unknown"

    return jsonify({
        "status": "success",
        "version": version,
        "branch": branch,
        "current_commit": current_commit,
        "latest_commit": latest_commit,
        "commits_behind": commits_behind,
        "update_available": commits_behind > 0,
        "commit_messages": commits[:20]  # Cap at 20 for the UI
    }), 200

@app.route("/api/update", methods=["POST"])
def api_update():
    """Pull latest code from GitHub, reinstall deps, and restart the hub."""
    logger.info("Software update requested via dashboard.")

    # 1. Kill all running agents
    killed = 0
    for agent_name, pid in list(launched_agent_processes.items()):
        try:
            os.killpg(pid, signal.SIGTERM)
            logger.info(f"Update: Terminated agent '{agent_name}' (PID: {pid})")
            killed += 1
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.error(f"Update: Error killing agent '{agent_name}' (PID: {pid}): {e}")
    launched_agent_processes.clear()
    logger.info(f"Update: Killed {killed} agent(s).")

    # 2. Get current branch
    ok, branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch if ok else "develop"

    # 3. Pull latest code
    ok, pull_out, pull_err = _git(["pull", "origin", branch], timeout=60)
    if not ok:
        error_msg = pull_err or pull_out or "Unknown git pull error"
        logger.error(f"Update: git pull failed: {error_msg}")
        return jsonify({
            "status": "error",
            "message": f"Git pull failed: {error_msg}",
            "hint": "You may have local changes. Try: git stash && git pull"
        }), 500

    logger.info(f"Update: git pull succeeded: {pull_out}")

    # 4. Reinstall dependencies
    req_path = os.path.join(_REPO_DIR, "requirements.txt")
    if os.path.exists(req_path):
        try:
            pip_result = _subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req_path, "-q"],
                capture_output=True, text=True, timeout=120, cwd=_REPO_DIR
            )
            if pip_result.returncode != 0:
                logger.warning(f"Update: pip install had issues: {pip_result.stderr}")
            else:
                logger.info("Update: Dependencies reinstalled successfully.")
        except Exception as e:
            logger.warning(f"Update: pip install failed: {e}")

    # 5. Read new version
    new_version = _get_current_version()
    logger.info(f"Update: New version is {new_version}")

    # 6. Schedule restart (give time for the response to be sent)
    def _restart():
        time.sleep(2)
        logger.info("Update: Restarting hub process...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_restart, daemon=True).start()

    return jsonify({
        "status": "success",
        "message": f"Updated to {new_version}. Hub is restarting...",
        "new_version": new_version,
        "git_output": pull_out
    }), 200


import subprocess

@app.route("/launch_agent", methods=["POST"])
def launch_agent():
    data = request.json
    agent_name = data.get("agent_name")
    connector_type = data.get("connector_type")
    is_proactive = data.get("is_proactive", False)
    initial_goal = data.get("initial_goal", None)
    execution_mode = data.get("execution_mode", "unrestricted") # Default to unrestricted for autonomous operation
    batch_experience = data.get("batch_experience", False)
    proactive_interval = data.get("proactive_interval", 600)

    if not agent_name:
        return jsonify({"status": "error", "message": "Agent name is required."}), 400

    # Construct the command to run main_agent_entrypoint.py
    cmd = [
        sys.executable,
        os.path.join(os.path.dirname(__file__), "main_agent_entrypoint.py"),
        "--num-agents", "1",
        "--agent-names", agent_name,
        "--connector-types", connector_type,
        "--proactive-loops", "yes" if is_proactive else "no",
        "--execution-modes", execution_mode,
        "--batch-experience", "yes" if batch_experience else "no",
        "--proactive-interval", str(proactive_interval)
    ]
    if initial_goal is not None:
        cmd.extend(["--initial-goal", initial_goal])

    try:
        # Launch the agent process
        # Using preexec_fn=os.setsid to detach the child process from the parent
        # so it continues to run even if the hub process is restarted.
        # Redirect stdout/stderr to files for debugging.
        agent_stdout_path = os.path.join(os.path.dirname(__file__), f"agent_{agent_name}_stdout.log")
        agent_stderr_path = os.path.join(os.path.dirname(__file__), f"agent_{agent_name}_stderr.log")

        # Explicitly set PYTHONPATH for the subprocess
        env = os.environ.copy()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        if 'PYTHONPATH' in env:
            env['PYTHONPATH'] = f"{project_root}:{env['PYTHONPATH']}"
        else:
            env['PYTHONPATH'] = project_root

        # --- Per-agent LLM provider override ---
        llm_provider = data.get("llm_provider", "")
        llm_api_key = data.get("llm_api_key", "")
        llm_model = data.get("llm_model", "")

        if llm_provider:
            # Map provider name to env var prefixes
            provider_map = {
                "openrouter": {"key": "OPENROUTER_API_KEY", "model": "OPENROUTER_MODEL", "enable": "ENABLE_OPENROUTER"},
                "huggingface": {"key": "HUGGINGFACE_API_KEY", "model": "HUGGINGFACE_MODEL", "enable": "ENABLE_HUGGINGFACE"},
                "ollama":      {"key": None,                  "model": "OLLAMA_MODEL",      "enable": "ENABLE_OLLAMA"},
                "moonshot":    {"key": "MOONSHOT_API_KEY",    "model": "MOONSHOT_MODEL",    "enable": "ENABLE_MOONSHOT_AI"},
            }
            pinfo = provider_map.get(llm_provider)
            if pinfo:
                # Disable all providers first, then enable only the selected one
                for p in provider_map.values():
                    env[p["enable"]] = "no"
                env[pinfo["enable"]] = "yes"
                if pinfo["key"] and llm_api_key:
                    env[pinfo["key"]] = llm_api_key
                if llm_model:
                    env[pinfo["model"]] = llm_model
                logger.info(f"Agent '{agent_name}' using per-agent LLM: provider={llm_provider}, model={llm_model or '(default)'}")

        with open(agent_stdout_path, "w") as stdout_file, open(agent_stderr_path, "w") as stderr_file:
            # Explicitly redirect stdin to /dev/null to ensure non-interactivity
            with open(os.devnull, 'r') as devnull:
                process = subprocess.Popen(cmd, stdin=devnull, stdout=stdout_file, stderr=stderr_file, preexec_fn=os.setsid, env=env)
                launched_agent_processes[agent_name] = process.pid # Store the PID
                launched_agent_configs[agent_name] = {"cmd": cmd, "env": env}

        logger.info(f"Launched agent '{agent_name}' with command: {' '.join(cmd)}")
        _add_notification("agent_launched", f"Agent '{agent_name}' launched")
        return jsonify({"status": "success", "message": f"Agent '{agent_name}' launched successfully!"}), 200
    except Exception as e:
        logger.error(f"Error launching agent '{agent_name}': {e}")
        return jsonify({"status": "error", "message": f"Failed to launch agent: {e}"}), 500


# ─── Workflow API Endpoints ───

@app.route("/api/workflows", methods=["GET"])
def api_list_workflows():
    return jsonify({"status": "success", "workflows": hub_memory["workflows"]}), 200

@app.route("/api/workflows", methods=["POST"])
def api_create_workflow():
    data = request.json
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Workflow name is required."}), 400
    steps = data.get("steps", [])
    if not steps:
        return jsonify({"status": "error", "message": "At least one step is required."}), 400

    # Auto-assign step IDs if not provided
    for i, step in enumerate(steps):
        if not step.get("id"):
            step["id"] = f"step{i+1}"

    workflow = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "description": data.get("description", ""),
        "steps": steps,
        "created_at": time.time(),
        "enabled": True
    }
    hub_memory["workflows"].append(workflow)
    _save_workflows()
    logger.info(f"[Workflows] Created workflow '{name}' with {len(steps)} step(s).")
    return jsonify({"status": "success", "workflow": workflow}), 200

@app.route("/api/workflows/<workflow_id>", methods=["GET"])
def api_get_workflow(workflow_id):
    for w in hub_memory["workflows"]:
        if w["id"] == workflow_id:
            return jsonify({"status": "success", "workflow": w}), 200
    return jsonify({"status": "error", "message": "Workflow not found."}), 404

@app.route("/api/workflows/<workflow_id>", methods=["PUT"])
def api_update_workflow(workflow_id):
    data = request.json
    for w in hub_memory["workflows"]:
        if w["id"] == workflow_id:
            if data.get("name"):
                w["name"] = data["name"].strip()
            if "description" in data:
                w["description"] = data["description"]
            if "steps" in data:
                steps = data["steps"]
                for i, step in enumerate(steps):
                    if not step.get("id"):
                        step["id"] = f"step{i+1}"
                w["steps"] = steps
            _save_workflows()
            return jsonify({"status": "success", "workflow": w}), 200
    return jsonify({"status": "error", "message": "Workflow not found."}), 404

@app.route("/api/workflows/<workflow_id>", methods=["DELETE"])
def api_delete_workflow(workflow_id):
    for i, w in enumerate(hub_memory["workflows"]):
        if w["id"] == workflow_id:
            hub_memory["workflows"].pop(i)
            _save_workflows()
            return jsonify({"status": "success", "message": "Workflow deleted."}), 200
    return jsonify({"status": "error", "message": "Workflow not found."}), 404

@app.route("/api/workflows/<workflow_id>/toggle", methods=["POST"])
def api_toggle_workflow(workflow_id):
    for w in hub_memory["workflows"]:
        if w["id"] == workflow_id:
            w["enabled"] = not w.get("enabled", True)
            _save_workflows()
            return jsonify({"status": "success", "enabled": w["enabled"]}), 200
    return jsonify({"status": "error", "message": "Workflow not found."}), 404

@app.route("/api/workflows/<workflow_id>/run", methods=["POST"])
def api_run_workflow(workflow_id):
    data = request.json or {}
    trigger_input = data.get("input", "")

    workflow = None
    for w in hub_memory["workflows"]:
        if w["id"] == workflow_id:
            workflow = w
            break
    if not workflow:
        return jsonify({"status": "error", "message": "Workflow not found."}), 404
    if not workflow.get("enabled", True):
        return jsonify({"status": "error", "message": "Workflow is disabled."}), 400
    if not workflow.get("steps"):
        return jsonify({"status": "error", "message": "Workflow has no steps."}), 400

    # Create a new run
    first_step = workflow["steps"][0]
    run = {
        "run_id": str(uuid.uuid4())[:8],
        "workflow_id": workflow["id"],
        "workflow_name": workflow["name"],
        "status": "running",
        "trigger_input": trigger_input,
        "started_at": time.time(),
        "completed_at": None,
        "current_step_id": first_step["id"],
        "step_results": {}
    }
    hub_memory["workflow_runs"].append(run)
    _add_notification("workflow_started", f"Workflow '{workflow['name']}' started")

    # Check if first step needs approval
    if first_step.get("wait_for_approval"):
        run["status"] = "paused"
        run["step_results"][first_step["id"]] = {
            "status": "waiting_approval",
            "agent_name": first_step["agent_name"],
            "started_at": None,
            "completed_at": None,
            "output": None
        }
        _save_workflow_runs()
        _add_notification("workflow_paused", f"Workflow '{workflow['name']}' paused — awaiting approval for step '{first_step['name']}'")
    else:
        # Execute first step
        _execute_workflow_step(run, workflow, first_step)

    return jsonify({"status": "success", "run_id": run["run_id"]}), 200

@app.route("/api/workflow_runs", methods=["GET"])
def api_list_workflow_runs():
    # Return most recent first, capped at 50
    runs = sorted(hub_memory["workflow_runs"], key=lambda r: r.get("started_at", 0), reverse=True)[:50]
    return jsonify({"status": "success", "runs": runs}), 200

@app.route("/api/workflow_runs/<run_id>", methods=["GET"])
def api_get_workflow_run(run_id):
    for r in hub_memory["workflow_runs"]:
        if r["run_id"] == run_id:
            return jsonify({"status": "success", "run": r}), 200
    return jsonify({"status": "error", "message": "Run not found."}), 404

@app.route("/api/workflow_runs/<run_id>/approve", methods=["POST"])
def api_approve_workflow_step(run_id):
    for run in hub_memory["workflow_runs"]:
        if run["run_id"] == run_id:
            if run["status"] != "paused":
                return jsonify({"status": "error", "message": "Run is not paused."}), 400
            step_id = run["current_step_id"]
            # Find workflow and step definition
            workflow = None
            for w in hub_memory["workflows"]:
                if w["id"] == run["workflow_id"]:
                    workflow = w
                    break
            if not workflow:
                return jsonify({"status": "error", "message": "Workflow definition not found."}), 404

            step_def = None
            for s in workflow["steps"]:
                if s["id"] == step_id:
                    step_def = s
                    break
            if not step_def:
                return jsonify({"status": "error", "message": "Step definition not found."}), 404

            # Execute the approved step
            _execute_workflow_step(run, workflow, step_def)
            return jsonify({"status": "success", "message": f"Step '{step_def['name']}' approved and executing."}), 200
    return jsonify({"status": "error", "message": "Run not found."}), 404

@app.route("/api/workflow_runs/<run_id>/cancel", methods=["POST"])
def api_cancel_workflow_run(run_id):
    for run in hub_memory["workflow_runs"]:
        if run["run_id"] == run_id:
            if run["status"] in ("completed", "failed", "cancelled"):
                return jsonify({"status": "error", "message": f"Run is already {run['status']}."}), 400
            run["status"] = "cancelled"
            run["completed_at"] = time.time()
            _save_workflow_runs()
            _add_notification("workflow_failed", f"Workflow '{run['workflow_name']}' was cancelled")
            return jsonify({"status": "success", "message": "Workflow run cancelled."}), 200
    return jsonify({"status": "error", "message": "Run not found."}), 404


# ─── Export / Import Settings ───

from flask import send_file
import io

@app.route("/api/export_settings", methods=["GET"])
def api_export_settings():
    """Export all settings (webhooks, templates, workflows, scheduled tasks, notifications) as a zip file."""
    buf = io.BytesIO()
    base_dir = os.path.dirname(__file__)
    files_to_export = [
        "webhooks.json", "agent_templates.json", "workflows.json",
        "workflow_runs.json", "scheduled_tasks.json", "notifications.json",
        "agent_groups.json"
    ]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in files_to_export:
            fpath = os.path.join(base_dir, fname)
            if os.path.exists(fpath):
                zf.write(fpath, fname)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="fulmen_settings_backup.zip")

@app.route("/api/import_settings", methods=["POST"])
def api_import_settings():
    """Import settings from a zip file backup. Merges/replaces the JSON config files."""
    # Accept either "settings_zip" (from dashboard form) or "file" as the upload key
    f = request.files.get("settings_zip") or request.files.get("file")
    if not f:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400
    if not f.filename.endswith(".zip"):
        return jsonify({"status": "error", "message": "File must be a .zip archive."}), 400

    base_dir = os.path.dirname(__file__)
    allowed_files = {
        "webhooks.json", "agent_templates.json", "workflows.json",
        "workflow_runs.json", "scheduled_tasks.json", "notifications.json",
        "agent_groups.json"
    }
    imported = []
    try:
        with zipfile.ZipFile(f.stream, "r") as zf:
            for name in zf.namelist():
                if name in allowed_files:
                    zf.extract(name, base_dir)
                    imported.append(name)
        # Reload all data from disk
        _load_webhooks()
        _load_agent_templates()
        _load_workflows()
        _load_workflow_runs()
        _load_scheduled_tasks()
        _load_notifications()
        _load_agent_groups()
        logger.info(f"[Import] Imported settings: {imported}")
        return jsonify({"status": "success", "message": f"Imported {len(imported)} file(s): {', '.join(imported)}"}), 200
    except Exception as e:
        logger.error(f"[Import] Error importing settings: {e}")
        return jsonify({"status": "error", "message": f"Import failed: {e}"}), 500


# ─── Agent Cloning ───

@app.route("/api/clone_agent/<agent_name>", methods=["POST"])
def api_clone_agent(agent_name):
    """Clone a running agent's config into a new agent with a different name."""
    data = request.json or {}
    new_name = data.get("new_name", "").strip()
    if not new_name:
        return jsonify({"status": "error", "message": "New agent name is required."}), 400
    if new_name in hub_memory["active_agents"]:
        return jsonify({"status": "error", "message": f"Agent '{new_name}' is already running."}), 400
    if new_name in launched_agent_processes:
        return jsonify({"status": "error", "message": f"Agent '{new_name}' already has a running process."}), 400

    # Find the original agent's launch config from the process command
    if agent_name not in launched_agent_processes:
        return jsonify({"status": "error", "message": f"Cannot clone — agent '{agent_name}' was not launched from this hub."}), 400

    # We can't recover the original launch args from the PID,
    # so we launch the clone with the same defaults. The user can customize via the form.
    # Build a basic launch command with the same defaults
    try:
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(__file__), "main_agent_entrypoint.py"),
            "--num-agents", "1",
            "--agent-names", new_name,
            "--connector-types", "none",
            "--proactive-loops", os.environ.get("DEFAULT_PROACTIVE_LOOPS", "yes"),
            "--execution-modes", os.environ.get("DEFAULT_EXECUTION_MODE", "unrestricted"),
            "--batch-experience", os.environ.get("DEFAULT_BATCH_EXPERIENCE", "no"),
            "--proactive-interval", os.environ.get("DEFAULT_PROACTIVE_INTERVAL", "600"),
        ]

        agent_stdout_path = os.path.join(os.path.dirname(__file__), f"agent_{new_name}_stdout.log")
        agent_stderr_path = os.path.join(os.path.dirname(__file__), f"agent_{new_name}_stderr.log")
        env = os.environ.copy()

        with open(agent_stdout_path, "w") as stdout_file, open(agent_stderr_path, "w") as stderr_file:
            with open(os.devnull, 'r') as devnull:
                process = subprocess.Popen(cmd, stdin=devnull, stdout=stdout_file, stderr=stderr_file, preexec_fn=os.setsid, env=env)
                launched_agent_processes[new_name] = process.pid
                launched_agent_configs[new_name] = {"cmd": cmd, "env": env}

        logger.info(f"Cloned agent '{agent_name}' as '{new_name}' (PID: {process.pid})")
        _add_notification("agent_launched", f"Agent '{new_name}' cloned from '{agent_name}'")
        return jsonify({"status": "success", "message": f"Agent '{new_name}' cloned and launched."}), 200
    except Exception as e:
        logger.error(f"Error cloning agent '{agent_name}' as '{new_name}': {e}")
        return jsonify({"status": "error", "message": f"Clone failed: {e}"}), 500


# ─── Auto-Restart API ───

@app.route("/api/auto_restart", methods=["GET"])
def api_get_auto_restart():
    return jsonify({"status": "success", "enabled": auto_restart_enabled}), 200

@app.route("/api/auto_restart", methods=["POST"])
def api_toggle_auto_restart():
    global auto_restart_enabled
    data = request.json or {}
    auto_restart_enabled = bool(data.get("enabled", not auto_restart_enabled))
    logger.info(f"[AutoRestart] Auto-restart {'enabled' if auto_restart_enabled else 'disabled'}")
    return jsonify({"status": "success", "enabled": auto_restart_enabled}), 200


if __name__ == "__main__":
    logger.info(f"Hub server starting on http://0.0.0.0:5000")
    serve(app, host="0.0.0.0", port=5000)
