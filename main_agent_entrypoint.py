
import logging
import os
import time
import sys
import threading
import importlib
import requests
from rich.logging import RichHandler
from rich.console import Console
from rich.prompt import Prompt
import subprocess # Added subprocess import
import socket # ADDED: For dynamic port finding

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# from agent_network.src.agent import Agent # Removed initial import

from dotenv import load_dotenv

# --- Configuration ---
# Load environment variables from .env file in the parent directory
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:5000")

# --- Logging Setup ---
log_file_path = os.path.join(os.path.dirname(__file__), "agent_debug.log")
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False), logging.FileHandler(log_file_path)]
)
# Reduce verbosity of other libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
console = Console()

# Global event to signal the main thread to shut down
main_shutdown_event = threading.Event()

def start_hub_if_not_running():
    """Starts the hub in a separate process if it's not already running."""
    try:
        requests.get(f"{HUB_URL}/get_active_agents", timeout=2)
        logger.info("Hub is already running.")
    except requests.exceptions.ConnectionError:
        logger.info("Hub not detected, starting it in a separate process...")
        
        hub_stdout_path = os.path.join(os.path.dirname(__file__), "hub_stdout.log")
        hub_stderr_path = os.path.join(os.path.dirname(__file__), "hub_stderr.log")

        with open(hub_stdout_path, "w") as stdout_file, open(hub_stderr_path, "w") as stderr_file:
            # Start the hub as a separate process
            hub_process = subprocess.Popen(
                [sys.executable, os.path.join(os.path.dirname(__file__), "hub.py")],
                stdout=stdout_file,
                stderr=stderr_file,
                preexec_fn=os.setsid # Detach from parent process group
            )
            logger.info(f"Hub process started with PID: {hub_process.pid}")
            
            # Poll the hub until it's ready
            for _ in range(30): # Try for up to 30 seconds
                try:
                    requests.get(f"{HUB_URL}/get_active_agents", timeout=1)
                    logger.info("Hub started successfully.")
                    return
                except requests.exceptions.ConnectionError:
                    time.sleep(1)
            
            logger.error("Fatal: Hub did not start within the expected time. Exiting.")
            sys.exit(1)

def get_connector_class(connector_type: str):
    """Dynamically imports and returns a connector class from the .connectors module."""
    try:
        module_name = f"agent_network.connectors.{connector_type}_connector"
        class_name = f"{connector_type.capitalize()}Connector"
        module = importlib.import_module(module_name)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        logger.error(f"Could not load connector '{connector_type}'. Ensure 'connectors/{connector_type}_connector.py' and class '{class_name}' exist.")
        logger.error(f"Details: {e}")
        return None

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0)) # Bind to a random free port
        return s.getsockname()[1] # Return the port number

def main():
    BANNER = """
---------------------------------------------------
 F U L M E N • A G E N T
 The Coding Storm
---------------------------------------------------
"""
    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print("[bold blue]Initializing Agent Network...[/bold blue]")
    start_hub_if_not_running()

    # Set GEMINI_NON_INTERACTIVE if running in a non-interactive environment
    if not sys.stdin.isatty():
        os.environ["GEMINI_NON_INTERACTIVE"] = "true"
        logger.info("Detected non-interactive environment, setting GEMINI_NON_INTERACTIVE=true.")
    
    active_connectors = []
    
    import argparse

    parser = argparse.ArgumentParser(description="Launch agent network with configurable agents.")
    parser.add_argument("--num-agents", type=int, default=1,
                        help="Number of agents to launch. Defaults to 1.")
    parser.add_argument("--agent-names", type=str,
                        help="Comma-separated list of agent names. If fewer than num-agents, defaults will be used.")
    parser.add_argument("--connector-types", type=str, default="",
                        help="Comma-separated list of connector types (discord, telegram, line, none). Defaults to 'none' for all.")
    parser.add_argument("--proactive-loops", type=str,
                        default=os.environ.get("DEFAULT_PROACTIVE_LOOPS", "no"),
                        help="Comma-separated list of 'yes' or 'no' for proactive loops. Defaults to 'no' or DEFAULT_PROACTIVE_LOOPS env var.")
    parser.add_argument("--execution-modes", type=str,
                        default=os.environ.get("DEFAULT_EXECUTION_MODE", "safe"),
                        help="Comma-separated list of execution modes for agents. Defaults to 'safe' or DEFAULT_EXECUTION_MODE env var.")
    parser.add_argument("--batch-experience", type=str,
                        default=os.environ.get("DEFAULT_BATCH_EXPERIENCE", "no"),
                        help="Comma-separated list of 'yes' or 'no' for batch experience. Defaults to 'no' or DEFAULT_BATCH_EXPERIENCE env var.")
    parser.add_argument("--proactive-interval", type=int,
                        default=int(os.environ.get("DEFAULT_PROACTIVE_INTERVAL", "600")),
                        help="Proactive loop interval in seconds. Defaults to 600 or DEFAULT_PROACTIVE_INTERVAL env var.")
    parser.add_argument("--initial-goal", type=str, default=None,
                        help="Initial goal for the agent.")
    args = parser.parse_args()

    active_connectors = []
    all_agents = [] # New: Keep track of all agent instances

    # Force reload modules to ensure latest changes are picked up
    import agent_network.src.memory
    importlib.reload(agent_network.src.memory)
    Memory = agent_network.src.memory.Memory

    import agent_network.src.agent
    importlib.reload(agent_network.src.agent)
    Agent = agent_network.src.agent.Agent # Re-import Agent after reload

    import agent_network.src.planner
    importlib.reload(agent_network.src.planner)

    import agent_network.tools.base_tool
    importlib.reload(agent_network.tools.base_tool)

    num_agents = args.num_agents
    agent_names_arg = [name.strip() for name in args.agent_names.split(',')] if args.agent_names else []
    connector_types_arg = [ct.strip() for ct in args.connector_types.split(',')] if args.connector_types else []
    proactive_loops_arg = [pl.strip().lower() == 'yes' for pl in args.proactive_loops.split(',')] if args.proactive_loops else []

    # --- Loop for Each Agent ---
    for i in range(num_agents):
        # Determine agent name
        if i < len(agent_names_arg):
            agent_name = agent_names_arg[i]
        else:
            default_agent_name = f"Agent{i+1}"
            # If running non-interactively (e.g., in a script), don't prompt (this will be skipped in non-interactive mode due to GEMINI_NON_INTERACTIVE)
            if sys.stdin.isatty():
                agent_name = Prompt.ask(f"[bold cyan]Enter name for Agent {i+1}[/bold cyan]", default=default_agent_name)
            else:
                agent_name = default_agent_name

        agent_port = find_free_port() # Assign a free port to each agent
        os.environ['AGENT_PORT'] = str(agent_port) # Set AGENT_PORT for the current agent
        
        # Determine proactive loop - Initialize with a default value
        is_proactive = proactive_loops_arg[i] if i < len(proactive_loops_arg) else False
        if not is_proactive and sys.stdin.isatty():
            is_proactive = Prompt.ask(
                f"[bold cyan]Should {agent_name} run a proactive loop (24/7 autonomous operation)?[/bold cyan]",
                choices=["yes", "no"],
                default="no"
            ).lower() == "yes"

        # Determine execution_mode, batch_experience, and proactive_interval based on parsed arguments
        execution_mode_val = args.execution_modes.split(',')[i] if args.execution_modes and i < len(args.execution_modes.split(',')) else args.execution_modes.split(',')[0]
        batch_experience_val = args.batch_experience.split(',')[i].lower() == 'yes' if args.batch_experience and i < len(args.batch_experience.split(',')) else args.batch_experience.split(',')[0].lower() == 'yes'
        proactive_interval_val = args.proactive_interval

        agent = Agent(name=agent_name, hub_url=HUB_URL, port=agent_port,
                      is_proactive=is_proactive,
                      batch_experience=batch_experience_val,
                      execution_mode=execution_mode_val,
                      proactive_interval=proactive_interval_val,
                      initial_goal=args.initial_goal,
                      shutdown_event=main_shutdown_event)
        all_agents.append(agent) # Add agent to the list of all agents

        # Determine connector type
        if i < len(connector_types_arg) and connector_types_arg[i] in ["discord", "telegram", "slack", "line", "voice", "whatsapp", "x", "none"]:
            connector_type_choice = connector_types_arg[i]
            console.print(f"  -> Using connector type '{connector_type_choice}' for {agent_name} from arguments.")
        else:
            # If running non-interactively, default to 'none'
            if sys.stdin.isatty():
                connector_type_choice = Prompt.ask(
                    f"[bold cyan]Choose connector type for {agent_name}[/bold cyan]\n",
                    choices=["discord", "telegram", "slack", "line", "voice", "none"],
                    default="none"
                ).lower()
            else:
                connector_type_choice = "none"

        if connector_type_choice != "none":
            ConnectorClass = get_connector_class(connector_type_choice)
            if ConnectorClass:
                try:
                    connector = None
                    if connector_type_choice == "discord":
                        token = os.environ.get(f"{agent_name.upper()}_DISCORD_TOKEN")
                        channel_id = os.environ.get(f"{agent_name.upper()}_DISCORD_CHANNEL_ID")

                        if not token:
                            console.print("[bold yellow]Discord token not found in .env.[/bold yellow]")
                            if sys.stdin.isatty():
                                token = Prompt.ask("[bold cyan]Enter Discord Bot Token[/bold cyan]", password=True)
                            else:
                                logger.error(f"Discord token not provided for {agent_name} in non-interactive mode. Skipping Discord connector.")
                                continue # Skip this connector
                            console.print("[bold yellow]WARNING: For security and persistence, consider adding this to your .env file (e.g., DISCORDAGENT_DISCORD_TOKEN='your_token').[/bold yellow]")
                        if not channel_id:
                            console.print("[bold yellow]Discord channel ID not found in .env.[/bold yellow]")
                            if sys.stdin.isatty():
                                channel_id = Prompt.ask("[bold cyan]Enter Discord Channel ID[/bold cyan]")
                            else:
                                logger.error(f"Discord channel ID not provided for {agent_name} in non-interactive mode. Skipping Discord connector.")
                                continue # Skip this connector
                            console.print("[bold yellow]WARNING: For persistence, consider adding this to your .env file (e.g., DISCORDAGENT_DISCORD_CHANNEL_ID='your_channel_id').[/bold yellow]")

                        connector = ConnectorClass(agent=agent, logger=logger, token=token, channel_id=channel_id)

                    elif connector_type_choice == "telegram":
                        token = os.environ.get(f"{agent_name.upper()}_TELEGRAM_TOKEN")
                        allowed_chats_str = os.environ.get(f"{agent_name.upper()}_TELEGRAM_ALLOWED_CHATS", "")
                        allowed_chats = allowed_chats_str.split(',') if allowed_chats_str else []

                        if not token:
                            console.print("[bold yellow]Telegram token not found in .env.[/bold yellow]")
                            if sys.stdin.isatty():
                                token = Prompt.ask("[bold cyan]Enter Telegram Bot Token[/bold cyan]", password=True)
                            else:
                                logger.error(f"Telegram token not provided for {agent_name} in non-interactive mode. Skipping Telegram connector.")
                                continue # Skip this connector
                            console.print("[bold yellow]WARNING: For security and persistence, consider adding this to your .env file (e.g., TELEGRAMAGENT_TELEGRAM_TOKEN='your_token').[/bold yellow]")
                        if not allowed_chats:
                            console.print("[bold yellow]Telegram allowed chats not found in .env. The bot will respond to all messages.[/bold yellow]")

                        connector = ConnectorClass(agent=agent, logger=logger, token=token, allowed_chats=allowed_chats)

                    elif connector_type_choice == "slack":
                        bot_token = os.environ.get(f"{agent_name.upper()}_SLACK_BOT_TOKEN")
                        app_token = os.environ.get(f"{agent_name.upper()}_SLACK_APP_TOKEN")
                        slack_channel_id = os.environ.get(f"{agent_name.upper()}_SLACK_CHANNEL_ID", "")

                        if not bot_token:
                            console.print("[bold yellow]Slack bot token not found in .env.[/bold yellow]")
                            if sys.stdin.isatty():
                                bot_token = Prompt.ask("[bold cyan]Enter Slack Bot Token (xoxb-...)[/bold cyan]", password=True)
                            else:
                                logger.error(f"Slack bot token not provided for {agent_name} in non-interactive mode. Skipping Slack connector.")
                                continue
                        if not app_token:
                            console.print("[bold yellow]Slack app-level token not found in .env.[/bold yellow]")
                            if sys.stdin.isatty():
                                app_token = Prompt.ask("[bold cyan]Enter Slack App-Level Token (xapp-...)[/bold cyan]", password=True)
                            else:
                                logger.error(f"Slack app token not provided for {agent_name} in non-interactive mode. Skipping Slack connector.")
                                continue

                        connector = ConnectorClass(agent=agent, logger=logger, bot_token=bot_token, app_token=app_token, channel_id=slack_channel_id or None)

                    elif connector_type_choice == "line":
                        channel_access_token = os.environ.get(f"{agent_name.upper()}_LINE_CHANNEL_ACCESS_TOKEN")
                        channel_secret = os.environ.get(f"{agent_name.upper()}_LINE_CHANNEL_SECRET")

                        if not channel_access_token or not channel_secret:
                            console.print("[bold yellow]Line channel access token or secret not found in .env.[/bold yellow]")
                            if sys.stdin.isatty():
                                if not channel_access_token:
                                    channel_access_token = Prompt.ask("[bold cyan]Enter Line Channel Access Token[/bold cyan]", password=True)
                                if not channel_secret:
                                    channel_secret = Prompt.ask("[bold cyan]Enter Line Channel Secret[/bold cyan]", password=True)
                            else:
                                logger.error(f"Line credentials not provided for {agent_name} in non-interactive mode. Skipping Line connector.")
                                continue
                            console.print("[bold yellow]WARNING: For security and persistence, consider adding these to your .env file.[/bold yellow]")
                        
                        connector = ConnectorClass(agent=agent, logger=logger, channel_access_token=channel_access_token, channel_secret=channel_secret)

                    elif connector_type_choice == "voice":
                        voice_port = int(os.environ.get(f"{agent_name.upper()}_VOICE_PORT", "5002"))
                        connector = ConnectorClass(agent=agent, logger=logger, port=voice_port)

                    if connector:
                        active_connectors.append(connector)
                        thread = threading.Thread(target=connector.start, daemon=True)
                        thread.start()
                        logger.info(f"Started {connector_type_choice.capitalize()} connector for {agent_name} in a separate thread.")

                except Exception as e:
                    logger.error(f"Failed to initialize or start connector '{connector_type_choice}' for {agent_name}: {e}")
    agent_threads = []
    for agent in all_agents:
        agent_thread = threading.Thread(target=agent.run, args=(main_shutdown_event,), daemon=True)
        agent_threads.append(agent_thread)
        agent_thread.start()
        logger.info(f"Agent '{agent.agent_name}' started in a separate thread.")

    console.print("""
[bold green]Agent Network is running.[/bold green] Press Ctrl+C to shut down.""")
    
    try:
        main_shutdown_event.wait() # Keep main thread alive until shutdown signal
    except KeyboardInterrupt:
        console.print("""

[bold blue]Shutting down Agent Network...[/bold blue]""")
        # If Ctrl+C is pressed, ensure the hub also gets the signal
        if not main_shutdown_event.is_set():
            main_shutdown_event.set()
        for connector in active_connectors:
            logger.info(f"Stopping {type(connector).__name__}...")
            connector.stop()
        
        # New: Deregister all agents from the hub
        for agent in all_agents:
            try:
                requests.post(f"{HUB_URL}/shutdown_agent/{agent.agent_name}", timeout=5) # Increased timeout to 5 seconds
                logger.info(f"Deregistered agent '{agent.agent_name}' from hub.")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Failed to deregister agent '{agent.agent_name}' from hub: {e}")
            except KeyboardInterrupt:
                logger.warning(f"KeyboardInterrupt received while deregistering agent '{agent.agent_name}'. Continuing shutdown.")
                # Do not re-raise, let the loop continue for other agents if possible
                # The outer KeyboardInterrupt handler will eventually finish

        console.print("[bold green]All components stopped. Exiting.[/bold green]")

if __name__ == "__main__":
    main()