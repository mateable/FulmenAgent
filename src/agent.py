import json
import logging
import os
import requests
import time
import threading
from typing import Any, Dict, List, Optional, Tuple # Added Tuple
from urllib.parse import urljoin
import sys
import subprocess
import importlib.util # Added for dynamic plugin loading
from pathlib import Path # Added for path manipulation

# Imports for internal components
from agent_network.src.planner import Planner
from agent_network.src.memory import Memory
from agent_network.src.critic import Critic
from agent_network.src.executor import Executor

# Imports for tools
from agent_network.tools.base_tool import BaseTool
from agent_network.tools.file_tools import ReadFileTool, WriteFileTool, ListDirectoryTool
from agent_network.tools.shell_tool import RunShellCommandTool
from agent_network.tools.utility_tools import PrintTaskTool, WebFetchTool, ImageAnalysisTool, SendImageTool, ImageGenerationTool, FinishTaskTool, SendUserMessageTool, SendAgentMessageTool, LLMCallTool
from agent_network.tools.email_tools import EmailCheckTool, EmailSendTool
from agent_network.tools.calendar_tools import CalendarCheckTool, CalendarAddEventTool
from agent_network.tools.voice_tools import MakePhoneCallTool, TranscribeVoiceTool, SynthesizeSpeechTool, ColdCallTool, SendSMSTool, CheckCallStatusTool, GibberLinkCallTool
from agent_network.tools.contact_tools import AccessContactsTool

# Define a module-level logger
logger = logging.getLogger(__name__)

def _load_plugins(agent_name: str, hub_url: str) -> List[Tuple[str, Dict[str, Any], List[BaseTool]]]:
    """
    Dynamically loads tools from the plugins directory, checking hub for enable/disable status.
    Returns a list of (plugin_name, manifest, loaded_tools) for successfully loaded plugins.
    """
    loaded_plugins_data: List[Tuple[str, Dict[str, Any], List[BaseTool]]] = []
    plugins_dir = Path(__file__).parent.parent / "plugins"
    
    if not plugins_dir.is_dir():
        logger.info(f"Plugins directory not found at {plugins_dir}. No plugins will be loaded.")
        return []

    logger.info(f"Scanning for plugins in {plugins_dir}...")
    for plugin_path in plugins_dir.iterdir():
        if plugin_path.is_dir():
            manifest_path = plugin_path / "manifest.json"
            if manifest_path.is_file():
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                    
                    plugin_name = manifest.get('name', plugin_path.name)
                    
                    # --- Check Hub for Plugin Enable/Disable Status ---
                    try:
                        status_response = requests.get(urljoin(hub_url, f"/api/agent/{agent_name}/plugin_status/{plugin_name}"))
                        status_response.raise_for_status()
                        plugin_status = status_response.json().get("status", "enabled")
                        if plugin_status == "disabled":
                            logger.info(f"Plugin '{plugin_name}' is disabled by the hub. Skipping loading.")
                            continue
                    except requests.exceptions.RequestException as e:
                        logger.error(f"Could not get plugin status from hub for '{plugin_name}': {e}. Assuming enabled.")
                        # Continue loading if hub is unreachable or error, so plugin can still function.

                    entry_point = manifest.get('entry_point')
                    if not entry_point or ':' not in entry_point:
                        logger.warning(f"Plugin '{plugin_name}' manifest.json is missing or has an invalid 'entry_point'. Skipping.")
                        continue
                    
                    # NEW: Auto-install requirements conditionally
                    plugin_req_path = plugin_path / "requirements.txt"
                    if plugin_req_path.is_file():
                        try:
                            # Query hub for auto-install deps preference
                            auto_install_status_response = requests.get(urljoin(hub_url, "/api/settings/auto_install_deps"))
                            auto_install_status_response.raise_for_status()
                            auto_install_enabled = auto_install_status_response.json().get("status", False) # Default to False if hub is unreachable
                            
                            if auto_install_enabled:
                                logger.info(f"Plugin '{plugin_name}' has requirements.txt. Auto-installing dependencies...")
                                subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(plugin_req_path)])
                                logger.info(f"Successfully installed dependencies for plugin '{plugin_name}'.")
                            else:
                                logger.warning(f"Plugin '{plugin_name}' has a 'requirements.txt' file at '{plugin_req_path}', but auto-install is disabled. Please ensure these dependencies are installed manually (e.g., `pip install -r {plugin_req_path}`).")

                        except requests.exceptions.RequestException as e:
                            logger.error(f"Could not get auto-install deps status from hub: {e}. Skipping auto-install for plugin '{plugin_name}'.")
                        except subprocess.CalledProcessError as e:
                            logger.error(f"Failed to install dependencies for plugin '{plugin_name}'. Error: {e}. Skipping plugin.")
                            continue
                        except Exception as e:
                            logger.error(f"An unexpected error occurred during dependency installation for '{plugin_name}': {e}. Skipping plugin.")
                            continue


                    module_name, func_name = entry_point.split(':', 1)
                    
                    module_file_path = plugin_path / f"{module_name.replace('.', os.sep)}.py"
                    if not module_file_path.is_file():
                        logger.warning(f"Plugin '{plugin_name}' entry point module '{module_name}.py' not found at '{module_file_path}'. Skipping.")
                        continue

                    full_module_name = f"plugins.{plugin_path.name}.{module_name}"
                    spec = importlib.util.spec_from_file_location(full_module_name, module_file_path)
                    if spec is None:
                        logger.warning(f"Could not find spec for plugin module: {full_module_name}. Skipping.")
                        continue
                    
                    plugin_module = importlib.util.module_from_spec(spec)
                    sys.modules[full_module_name] = plugin_module
                    spec.loader.exec_module(plugin_module)
                    
                    get_tools_func = getattr(plugin_module, func_name, None)
                    if get_tools_func and callable(get_tools_func):
                        loaded_tools = get_tools_func()
                        # Use duck-typing check: verify tools have 'name', 'description', and 'run' attributes
                        # This avoids isinstance failures when BaseTool is loaded from different module paths via importlib
                        if isinstance(loaded_tools, list) and all(
                            hasattr(tool, 'name') and hasattr(tool, 'description') and hasattr(tool, 'run') and callable(tool.run)
                            for tool in loaded_tools
                        ):
                            loaded_plugins_data.append((plugin_name, manifest, loaded_tools))
                            logger.info(f"Successfully loaded plugin: {plugin_name} (Tools: {[t.name for t in loaded_tools]})")
                        else:
                            logger.warning(f"Plugin '{plugin_name}' entry point '{func_name}' did not return a list of BaseTool instances. Skipping.")
                    else:
                        logger.warning(f"Plugin '{plugin_name}' entry point '{func_name}' not found or not callable. Skipping.")

                except json.JSONDecodeError:
                    logger.error(f"Invalid manifest.json in plugin '{plugin_path.name}'. Skipping.")
                except Exception as e:
                    logger.error(f"Error loading plugin '{plugin_path.name}': {e}", exc_info=True)
                    
    return loaded_plugins_data


class Agent:
    def __init__(self, name: str = "LocalAgent", hub_url: str = None, port: int = 5001, connector: Any = None,
                 is_proactive: bool = False, execution_mode: str = "safe", batch_experience: bool = False,
                 proactive_interval: int = 600, initial_goal: str = None, shutdown_event: threading.Event = None):
        
        self.agent_name = name

        # Reconfigure logging for this specific agent instance
        log_file_path = os.path.join(os.path.dirname(__file__), "..", f"agent_{self.agent_name}_debug.log")
        # Ensure the logger is unique per agent instance to avoid duplicate handlers
        self.logger = logging.getLogger(f"agent_{self.agent_name}_logger")
        if self.logger.hasHandlers():
            self.logger.handlers.clear()
        self.logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(log_file_path, mode='w') # Overwrite log file each time
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s", datefmt="[%X]")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.info(f"Logger initialized for agent {self.agent_name} logging to {log_file_path}")

        self.hub_url = hub_url or os.environ.get("HUB_URL", "http://127.0.0.1:5000")
        self.port = port
        self.connector = connector
        self.is_proactive = is_proactive
        self.execution_mode = execution_mode
        self.batch_experience = batch_experience
        self.proactive_interval = proactive_interval
        self.initial_goal = initial_goal
        self.logger.debug(f"Initial goal for agent {self.agent_name}: {self.initial_goal if self.initial_goal else 'No initial goal set.'}")

        self.last_proactive_check = time.time()
        self.last_heartbeat_sent = time.time()
        self.MAX_MESSAGE_BATCH = 5 # Max messages to process per cycle
        self.experience_buffer = [] # Buffer for experiences before submitting in batch
        self.memory = Memory(agent_name=self.agent_name) # Initialize memory

        self.llm_provider_settings = self._load_llm_provider_settings() # Load settings from hub

        # Determine base model, with fallback
        self.base_model = os.environ.get("DEFAULT_LLM_MODEL", "google/gemini-pro") # Default LLM for planning if none specified
        
        # New: Store loaded plugin manifests
        self.plugin_manifests: Dict[str, Dict[str, Any]] = {}

        self.planner = Planner(
            agent_name=self.agent_name,
            base_model=self.base_model,
            memory=self.memory,
            tools={}, # Temporarily empty; tools will be fully populated by _initialize_tools
            llm_provider_settings=self.llm_provider_settings
        )

        # Initialize tools
        self.tools = self._initialize_tools()
        # Update planner's tools after they are all initialized
        self.planner.update_tools(self.tools)

        self.executor = Executor(self.tools)
        self.critic = Critic(self.planner, self.memory) # Critic also needs base_model and memory

        self.logger.info(f"Agent {self.agent_name} initialized. Proactive: {self.is_proactive}, Execution Mode: {self.execution_mode}, Batch Experience: {self.batch_experience}, Proactive Interval: {self.proactive_interval}s")


    def _initialize_tools(self) -> List[BaseTool]:
        """Initializes all available tools for the agent."""
        tools = [
            ReadFileTool(),
            WriteFileTool(),
            ListDirectoryTool(),
            RunShellCommandTool(),
            PrintTaskTool(),
            WebFetchTool(),
            SendUserMessageTool(),
            SendAgentMessageTool(),
            FinishTaskTool(),
            LLMCallTool(self.planner) # Pass the planner instance here
        ]

        # Add voice/phone tools if enabled (Twilio-based, no Google Cloud required)
        if os.environ.get("ENABLE_VOICE_TOOLS", "no").lower() == "yes":
            tools.extend([
                MakePhoneCallTool(),
                SendSMSTool(),
                CheckCallStatusTool(),
                SynthesizeSpeechTool(),
                TranscribeVoiceTool(),
                ColdCallTool(),
                GibberLinkCallTool()
            ])
            self.logger.info("Voice/phone tools enabled (Twilio).")
        else:
            self.logger.info("Voice tools not enabled. Set ENABLE_VOICE_TOOLS=yes in Admin Settings.")

        if os.environ.get("ENABLE_EMAIL_TOOLS", "no").lower() == "yes":
            tools.extend([
                EmailCheckTool(),
                EmailSendTool()
            ])
        else:
            self.logger.info("Email tools not enabled.")

        if os.environ.get("ENABLE_CALENDAR_TOOLS", "no").lower() == "yes":
            tools.extend([
                CalendarCheckTool(),
                CalendarAddEventTool()
            ])
        else:
            self.logger.info("Calendar tools not enabled.")
        
        if os.environ.get("ENABLE_CONTACT_TOOLS", "no").lower() == "yes":
            tools.append(AccessContactsTool())
        else:
            self.logger.info("Contact tools not enabled.")

        if os.environ.get("ENABLE_IMAGE_TOOLS", "no").lower() == "yes":
            tools.extend([
                ImageAnalysisTool(),
                ImageGenerationTool(),
                SendImageTool()
            ])
        else:
            self.logger.info("Image tools not enabled.")
        
        # Load tools from plugins
        loaded_plugins_data = _load_plugins(self.agent_name, self.hub_url)
        for plugin_name, manifest, plugin_tool_list in loaded_plugins_data:
            self.plugin_manifests[plugin_name] = manifest # Store manifest
            tools.extend(plugin_tool_list)

        return tools


    def _load_llm_provider_settings(self) -> Dict[str, Any]:
        """Loads LLM provider settings from the hub."""
        try:
            response = requests.get(urljoin(self.hub_url, "/api/config"))
            response.raise_for_status()
            config = response.json()
            settings = {
                "ENABLE_MOONSHOT_AI": config.get("ENABLE_MOONSHOT_AI", "no"),
                "ENABLE_OLLAMA": config.get("ENABLE_OLLAMA", "no"),
                "ENABLE_OPENROUTER": config.get("ENABLE_OPENROUTER", "no"),
                "ENABLE_VOYAGE_AI": config.get("ENABLE_VOYAGE_AI", "no"),
                "ENABLE_HUGGINGFACE": config.get("ENABLE_HUGGINGFACE", "no"),
            }
            self.logger.debug(f"LLM Provider Settings for {self.agent_name}: {settings}")
            return settings
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error loading LLM provider settings for {self.agent_name}: {e}")
            return {}

    def send_heartbeat(self):
        try:
            self.logger.info(f"Attempting to send heartbeat for agent {self.agent_name} to hub at {self.hub_url}...")
            # Include loaded plugin manifests and currently active plugin names in heartbeat
            active_plugin_names = list(self.plugin_manifests.keys()) # All plugins loaded by the agent
            
            payload = {
                "name": self.agent_name,
                "url": f"http://localhost:{os.environ.get('AGENT_PORT', '5001')}", # Agent's own URL (conceptual)
                "plugins": {
                    "manifests": self.plugin_manifests,
                    "active_names": active_plugin_names # For now, all loaded are active. Hub will determine global status.
                }
            }
            # NEW: Add available Ollama models to the heartbeat payload
            if hasattr(self, 'planner') and self.planner and hasattr(self.planner, 'available_ollama_models'):
                payload['ollama_models'] = [model.model_dump().get('name') for model in self.planner.available_ollama_models if model.model_dump().get('name')]

            response = requests.post(urljoin(self.hub_url, f"/api/heartbeat/{self.agent_name}"), json=payload)
            response.raise_for_status()
            self.logger.info(f"Heartbeat successfully sent for agent {self.agent_name}.")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error sending heartbeat for agent {self.agent_name}: {e}", exc_info=True)
            # sys.exit(1) # Temporarily removed to allow further logging

    def _handle_gibberlink_protocol(self, message: Dict[str, Any]) -> str:
        """Handle GibberLink AI-to-AI protocol markers in incoming messages.
        Returns the decoded/plain message text for normal processing."""
        raw = message.get("message", "")
        sender = message.get("sender", "unknown")

        # [GL:DATA] — compressed message, decompress it
        if raw.startswith("[GL:DATA]"):
            try:
                import zlib, base64
                compressed = base64.b64decode(raw[len("[GL:DATA]"):])
                decoded = zlib.decompress(compressed).decode("utf-8")
                self.logger.info(f"[GibberLink] Decoded compressed message from {sender}: '{decoded[:100]}...'")
                return decoded
            except Exception as e:
                self.logger.error(f"[GibberLink] Failed to decompress message from {sender}: {e}")
                return raw  # Fall back to raw message

        # [GL:HELLO] — handshake init from another AI agent, auto-respond with ACK
        if raw.startswith("[GL:HELLO]"):
            plain_msg = raw[len("[GL:HELLO]"):].strip()
            self.logger.info(f"[GibberLink] Handshake from {sender} — responding with ACK")

            # Send ACK back to the sender
            try:
                payload = {
                    "target_agent_name": sender,
                    "message": "[GL:ACK] Confirmed AI-to-AI link.",
                    "sender_agent_name": self.agent_name
                }
                hub_url = os.environ.get("HUB_URL", "http://127.0.0.1:5000")
                requests.post(f"{hub_url}/send_message_to_agent_by_name", json=payload, timeout=5)
            except Exception as e:
                self.logger.error(f"[GibberLink] Failed to send ACK to {sender}: {e}")

            # Mark handshake complete for this pair (so our future sends are compressed)
            try:
                from agent_network.plugins.gibberlink_plugin.tools import _handshake_state
                _handshake_state[f"{self.agent_name}->{sender}"] = True
            except ImportError:
                pass

            return plain_msg if plain_msg else None  # None means no further processing needed

        # [GL:ACK] — handshake confirmed by the other agent
        if raw.startswith("[GL:ACK]"):
            plain_msg = raw[len("[GL:ACK]"):].strip()
            self.logger.info(f"[GibberLink] Handshake confirmed by {sender} — compressed protocol active")

            # Mark handshake complete
            try:
                from agent_network.plugins.gibberlink_plugin.tools import _handshake_state
                _handshake_state[f"{self.agent_name}->{sender}"] = True
            except ImportError:
                pass

            return plain_msg if plain_msg else None

        # No protocol marker — regular message
        return raw

    def _process_single_message(self, message: Dict[str, Any]):
        self.logger.info(f"Agent {self.agent_name} processing message: {message}")
        self.logger.info(f"Agent {self.agent_name} received message: {message['message']}")

        # Handle GibberLink protocol (auto-detect AI agents, decompress data)
        task = self._handle_gibberlink_protocol(message)
        if task is None:
            self.logger.info(f"[GibberLink] Protocol-only message from {message.get('sender')}, no task to process.")
            return

        # Check if sender is a GibberLink-confirmed AI agent
        sender = message.get("sender", "unknown")
        is_ai_sender = False
        try:
            from agent_network.plugins.gibberlink_plugin.tools import _handshake_state
            is_ai_sender = _handshake_state.get(f"{self.agent_name}->{sender}", False)
        except ImportError:
            pass

        # If talking to an AI agent, tag the task for compact mode
        if is_ai_sender or message.get("message", "").startswith(("[GL:HELLO]", "[GL:DATA]")):
            task = f"[AI2AI:{sender}] {task}"
            self.logger.info(f"[GibberLink] Compact mode active for task from AI agent {sender}")

        experience_data = {
            "agent_name": self.agent_name,
            "task": task,
            "step": {"tool": "receive_message", "args": {"message": message["message"]}},
            "step_result": {"status": "success", "output": "Message received."},
            "reflection": {"feedback": "N/A", "distilled_tips": []},
            "token_usage": {"provider": "none", "prompt_tokens": 0, "completion_tokens": 0}
        }
        self.memory.add_experience(experience_data)
        self._process_task_with_replanning(task)

    def _process_task_with_replanning(self, task: str):
        total_task_token_usage = {"moonshot_ai": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "ollama": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "openrouter": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "voyage_ai": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "huggingface": {"prompt_tokens": 0, "completion_tokens": 0}}
        experiences = []
        plan_attempts = 0
        MAX_PLAN_ATTEMPTS = 3

        while plan_attempts < MAX_PLAN_ATTEMPTS:
            self.logger.info(f"Agent {self.agent_name} creating plan for task: {task} (Attempt {plan_attempts + 1})")
            
            plan_response = self.planner.create_plan(task, experiences) # Pass previous experiences for replanning
            current_plan = plan_response["plan"]
            
            # Aggregate token usage from planning
            for provider, usage in plan_response["token_usage"].items():
                if provider in total_task_token_usage:
                    total_task_token_usage[provider]["prompt_tokens"] += usage["prompt_tokens"]
                    total_task_token_usage[provider]["completion_tokens"] += usage["completion_tokens"]
            
            if not current_plan: # Check if plan is None or empty
                self.logger.warning(f"Agent {self.agent_name} failed to create a valid plan. Raw response: {current_plan}")
                feedback = f"Failed to create a valid plan after {plan_attempts + 1} attempts. LLM response: {plan_response.get('content', str(current_plan))}"
                reflection_response = self.planner.reflect(task, experiences + [{"status": "failed", "feedback": feedback}])
                self.memory.add_experience({
                    "agent_name": self.agent_name,
                    "task": task,
                    "step": {"tool": "planner", "args": {"task": task}},
                    "step_result": {"status": "failed", "output": feedback},
                    "reflection": reflection_response["reflection"],
                    "token_usage": reflection_response["token_usage"]
                })
                return # Give up on this task

            step_results = []
            for step in current_plan:
                tool_name = step.get("tool")
                tool_args = step.get("args", {})
                goal = step.get("goal", "No goal specified.")

                # Auto-inject agent_name for tools that need it (LLM doesn't know the agent's own name)
                if tool_name == "send_user_message" and "agent_name" not in tool_args:
                    tool_args["agent_name"] = self.agent_name
                if tool_name == "send_agent_message" and "sender_agent_name" not in tool_args:
                    tool_args["sender_agent_name"] = self.agent_name

                self.logger.info(f"Agent {self.agent_name} - Step Goal: {goal}, Tool: {tool_name}, Args: {tool_args}")

                tool_output = None
                status = "success"
                message = "Tool executed successfully."
                token_usage_step = {"provider": "none", "prompt_tokens": 0, "completion_tokens": 0}

                if tool_name and tool_name != "None":
                    tool_instance = self.planner.tools.get(tool_name)
                    if tool_instance:
                        if self.execution_mode == "safe":
                            approval_status = self._request_approval(tool_name, tool_args)
                            if approval_status == "approved":
                                try:
                                    # Execute the tool and capture output
                                    tool_output = self.executor.execute(tool_name, **tool_args)
                                except Exception as e:
                                    status = "failed"
                                    message = f"Error executing tool {tool_name}: {e}"
                                    self.logger.error(message)
                            else:
                                status = "denied"
                                message = f"Tool execution for {tool_name} was denied by user."
                                self.logger.warning(message)
                        else: # unrestricted mode
                            try:
                                # Execute the tool and capture output
                                tool_output = self.executor.execute(tool_name, **tool_args)
                            except Exception as e:
                                status = "failed"
                                message = f"Error executing tool {tool_name}: {e}"
                                self.logger.error(message)
                    else:
                        status = "failed"
                        message = f"Tool '{tool_name}' not found."
                        self.logger.warning(message)
                else: # tool_name is "None" or not provided, likely a reasoning step
                    tool_output = "No tool used, reasoning step."
                    status = "success"
                    message = "Reasoning step completed."

                step_results.append({
                    "step": step,
                    "tool_output": tool_output,
                    "status": status,
                    "message": message
                })
                
                # If a tool involves an LLM call internally, its token usage would be captured there.
                # If not, token_usage_step remains default.
                # For now, we assume _call_llm in planner handles token usage.

                experience_entry = {
                    "agent_name": self.agent_name,
                    "task": task,
                    "step": step,
                    "step_result": {"status": status, "output": str(tool_output) if tool_output else message},
                    "reflection": {"feedback": "N/A", "distilled_tips": []}, # Reflection will be added later
                    "token_usage": token_usage_step, # Placeholder for tool-specific token usage if applicable
                    "timestamp": time.time()
                }
                experiences.append(experience_entry) # Add to current experiences for replanning/reflection

            # Auto-send the final tool result to the user/agent
            plan_tool_names = [s.get("tool") for s in current_plan]
            if "send_user_message" not in plan_tool_names and "gibberlink_send" not in plan_tool_names and step_results:
                # Find the last successful tool output
                last_output = None
                for sr in reversed(step_results):
                    if sr["status"] == "success" and sr["tool_output"]:
                        output = sr["tool_output"]
                        if isinstance(output, dict):
                            last_output = output.get("output", str(output))
                        else:
                            last_output = str(output)
                        break
                if last_output and last_output != "No tool used, reasoning step.":
                    # If this task came from an AI agent, reply via GibberLink
                    if task.startswith("[AI2AI:"):
                        closing = task.index("]")
                        reply_to_agent = task[7:closing]
                        self.logger.info(f"[GibberLink] Auto-replying to AI agent {reply_to_agent}: {last_output[:80]}...")
                        gl_send = self.planner.tools.get("gibberlink_send")
                        if gl_send:
                            try:
                                gl_send.run(target_agent=reply_to_agent, message=last_output, sender_agent_name=self.agent_name)
                            except Exception as e:
                                self.logger.error(f"Error auto-sending GibberLink reply: {e}")
                    else:
                        self.logger.info(f"Auto-sending result to user: {last_output}")
                        send_tool = self.planner.tools.get("send_user_message")
                        if send_tool:
                            try:
                                send_tool.run(message=last_output, agent_name=self.agent_name, title=f"Result: {task[:50]}")
                            except Exception as e:
                                self.logger.error(f"Error auto-sending result to user: {e}")

            # After all steps in a plan attempt, reflect
            self.logger.info(f"Agent {self.agent_name} reflecting on task: {task}")
            reflection_response = self.planner.reflect(task, experiences)
            reflection = reflection_response["reflection"]
            
            # Aggregate token usage from reflection
            for provider, usage in reflection_response["token_usage"].items():
                if provider in total_task_token_usage:
                    total_task_token_usage[provider]["prompt_tokens"] += usage["prompt_tokens"]
                    total_task_token_usage[provider]["completion_tokens"] += usage["completion_tokens"]

            # Store the final experiences with reflection and aggregated token usage
            for exp in experiences:
                if exp["reflection"]["feedback"] == "N/A": # Only update if not already reflected
                    exp["reflection"] = reflection
                exp["token_usage"] = total_task_token_usage # Assign aggregated token usage to all experiences from this task
                self.memory.add_experience(exp) # Add to agent's memory
                self._submit_experience_to_hub(exp)

            # Check if task is complete, or if replanning is needed
            # For simplicity, if a plan completes without critical failure, we assume success.
            # More sophisticated logic could check reflection feedback for success criteria.
            if all(res["status"] != "failed" and res["status"] != "denied" for res in step_results):
                self.logger.info(f"Agent {self.agent_name} successfully completed task: {task}")
                return
            else:
                self.logger.warning(f"Agent {self.agent_name} encountered issues. Replanning for task: {task}")
                plan_attempts += 1
                # The 'experiences' list already contains the failed step, which will inform replanning

        self.logger.error(f"Agent {self.agent_name} failed to complete task: {task} after {MAX_PLAN_ATTEMPTS} attempts.")


    def _request_approval(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Requests user approval from the hub for a sensitive tool."""
        APPROVAL_TIMEOUT = 300  # 5 minutes max wait
        try:
            response = requests.post(
                urljoin(self.hub_url, "/api/request_approval"),
                json={"agent_name": self.agent_name, "tool_name": tool_name, "tool_args": tool_args}
            )
            response.raise_for_status()
            request_id = response.json().get("request_id")
            self.logger.info(f"Approval requested for {tool_name} with ID: {request_id}")

            # Poll the hub for approval status with timeout
            start_time = time.time()
            while time.time() - start_time < APPROVAL_TIMEOUT:
                time.sleep(5) # Poll every 5 seconds
                check_response = requests.get(urljoin(self.hub_url, f"/api/check_approval/{request_id}"))
                check_response.raise_for_status()
                status = check_response.json().get("status")
                if status in ["approved", "denied"]:
                    self.logger.info(f"Approval request {request_id} status: {status}")
                    return status

            self.logger.warning(f"Approval request {request_id} timed out after {APPROVAL_TIMEOUT}s.")
            return "denied"
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error requesting or checking approval: {e}")
            return "denied" # Default to denial on error

    def _submit_experience_to_hub(self, experience: Dict[str, Any]):
        if self.batch_experience:
            self.experience_buffer.append(experience)
            self.logger.debug(f"Experience buffered for batch submission ({len(self.experience_buffer)} experiences).")
        else:
            try:
                response = requests.post(urljoin(self.hub_url, "/submit_experience"), json=[experience])
                response.raise_for_status()
                self.logger.debug("Experience submitted to hub.")
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Error submitting experience to hub: {e}")

    def _flush_experience_buffer(self):
        if self.experience_buffer:
            try:
                response = requests.post(urljoin(self.hub_url, "/submit_experience"), json=self.experience_buffer)
                response.raise_for_status()
                self.logger.info(f"Flushed {len(self.experience_buffer)} experiences to hub.")
                self.experience_buffer = []
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Error flushing experience buffer to hub: {e}")

    def _fetch_agent_messages(self) -> List[Dict[str, Any]]:
        try:
            response = requests.get(urljoin(self.hub_url, f"/api/agent_message_queue/{self.agent_name}"))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching messages for agent {self.agent_name}: {e}")
            return []

    def _register_with_hub(self):
        try:
            
            # --- Diagnostic: Test basic connectivity to hub ---
            try:
                self.logger.info(f"Diagnostic: Pinging hub at {self.hub_url}...")
                diag_response = requests.get(self.hub_url, timeout=3)
                diag_response.raise_for_status()
                self.logger.info(f"Diagnostic: Ping to hub successful. Status code: {diag_response.status_code}")
            except Exception as diag_e:
                self.logger.error(f"Diagnostic: Ping to hub FAILED: {diag_e}", exc_info=True)
                # Do not exit here, continue to try registration, as it might be a specific endpoint issue.
            # --- End Diagnostic ---

            self.logger.info(f"Attempting to register agent {self.agent_name} with hub at {self.hub_url}...")
            response = requests.post(
                urljoin(self.hub_url, "/register_agent"),
                json={"name": self.agent_name, "url": f"http://localhost:{os.environ.get('AGENT_PORT', '5001')}"},
                timeout=5 # Add a 5-second timeout
            )
            response.raise_for_status()
            self.logger.info(f"Agent {self.agent_name} successfully registered with hub.")
        except Exception as e: # Broaden exception to catch any error
            self.logger.error(f"Critical error during agent registration: {e}", exc_info=True)
            # sys.exit(1) # Temporarily removed to allow further logging

    def _distill_user_insights_from_memories(self):
        self.logger.info(f"Agent {self.agent_name} distilling user insights from memories.")
        # Retrieve recent user messages or interactions from memory or hub
        # For now, let's simulate by pulling from hub_memory, but ideally from agent's own memory
        # Or, the planner uses the agent's memory to find relevant user messages
        
        # This part needs to be revised as agent's memory is now local.
        # The agent should query its own memory for recent user interactions.
        
        # Placeholder for actual implementation using self.memory
        recent_user_interactions = self.memory.retrieve_relevant_memories(query="recent user interactions", n_results=5)
        insights_task = "Distill key user insights and potential ongoing goals from these interactions."
        
        # Use planner to distill insights
        distill_prompt = f"""
        Given the following recent user interactions/memories:
        {json.dumps(recent_user_interactions, indent=2)}

        Analyze these and distill key user insights, underlying needs, or potential ongoing goals.
        Format your insights as a JSON array of strings, where each string is a concise insight.
        Example:
        ["User is frequently asking about project deadlines.", "User seems interested in automating report generation."]
        """
        response = self.planner.evaluate_prompt(distill_prompt)
        insights_content = response["content"]
        token_usage = response["token_usage"]

        # Add token usage to aggregated total for insights distillation
        self._aggregate_token_usage_for_agent_task(total_task_token_usage=self.memory.agent_total_token_usage, new_token_usage=token_usage)
        
        try:
            insights = json.loads(insights_content)
            if isinstance(insights, list):
                for insight in insights:
                    # Store insight in memory or submit to hub if needed
                    self.logger.info(f"Distilled Insight for {self.agent_name}: {insight}")
                    # Example: Add to memory (can be a special type of experience or directly a tip)
                    # self.memory.add_experience(self.agent_name, insights_task, {"tool": "distill_insights"}, {"status": "success", "output": insight}, {"feedback": "N/A", "distilled_tips": []}, token_usage)
            else:
                self.logger.warning(f"Distillation did not return a list: {insights_content}")
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode insights JSON: {insights_content}")
            
    def _generate_proactive_tasks_from_insights(self):
        self.logger.info(f"Agent {self.agent_name} generating proactive tasks from insights.")
        # Retrieve current distilled insights from memory
        # (This would be more sophisticated, e.g., querying for insights within a timeframe)
        
        # Placeholder for actual implementation using self.memory
        current_insights = self.memory.retrieve_relevant_memories(query="distilled user insights", n_results=3)
        if not current_insights:
            self.logger.info(f"No current insights to generate proactive tasks for {self.agent_name}.")
            return

        generate_task_prompt = f"""
        Given the following current user insights:
        {json.dumps(current_insights, indent=2)}

        Generate a list of highly relevant, actionable, and proactive tasks that the agent could undertake
        to better serve the user or address their implicit needs.
        Tasks should be concise and direct.
        Format your tasks as a JSON array of strings, where each string is a concise insight.
        Example:
        ["Monitor project management tool for new deadlines.", "Draft a report template for sales data."]
        """
        response = self.planner.evaluate_prompt(generate_task_prompt)
        proactive_tasks_content = response["content"]
        token_usage = response["token_usage"]

        # Add token usage to aggregated total for proactive tasks generation
        self._aggregate_token_usage_for_agent_task(total_task_token_usage=self.memory.agent_total_token_usage, new_token_usage=token_usage)

        try:
            proactive_tasks = json.loads(proactive_tasks_content)
            if isinstance(proactive_tasks, list):
                for task in proactive_tasks:
                    self.logger.info(f"Generated Proactive Task for {self.agent_name}: {task}")
                    # Process this proactive task (e.g., add to a queue or process directly)
                    self._process_task_with_replanning(task) # Directly process generated tasks
            else:
                self.logger.warning(f"Proactive task generation did not return a list: {proactive_tasks_content}")
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode proactive tasks JSON: {proactive_tasks_content}")

    def _aggregate_token_usage_for_agent_task(self, total_task_token_usage: Dict[str, Any], new_token_usage: Dict[str, Any]):
        """Helper to aggregate token usage within agent tasks."""
        provider = new_token_usage.get("provider", "none")
        if provider != "none":
            if provider not in total_task_token_usage:
                total_task_token_usage[provider] = {"prompt_tokens": 0, "completion_tokens": 0}
            total_task_token_usage[provider]["prompt_tokens"] += new_token_usage.get("prompt_tokens", 0)
            total_task_token_usage[provider]["completion_tokens"] += new_token_usage.get("completion_tokens", 0)

    def run(self, shutdown_event: threading.Event):
        self.logger.info(f"Agent {self.agent_name} starting.")
        self._register_with_hub()

        # Process initial goal if one was provided at launch
        if self.initial_goal:
            self.logger.info(f"Agent {self.agent_name} processing initial goal: {self.initial_goal}")
            self._process_single_message({"sender": "user", "message": self.initial_goal})
            self._flush_experience_buffer()

        while not shutdown_event.is_set():
            current_time = time.time()

            # Send heartbeat periodically
            if current_time - self.last_heartbeat_sent >= 10: # Send heartbeat every 10 seconds
                self.send_heartbeat()
                self.last_heartbeat_sent = current_time

            # Fetch and process messages from the hub
            messages = self._fetch_agent_messages()
            if messages:
                self.logger.info(f"Agent {self.agent_name} fetched {len(messages)} messages.")
                for message in messages[:self.MAX_MESSAGE_BATCH]: # Process in batches
                    self._process_single_message(message)
                self._flush_experience_buffer() # Flush buffer after processing messages

            # Proactive loop if enabled
            if self.is_proactive and (current_time - self.last_proactive_check >= self.proactive_interval):
                self.logger.info(f"Agent {self.agent_name} performing proactive tasks.")
                self._distill_user_insights_from_memories()
                self._generate_proactive_tasks_from_insights()
                self.last_proactive_check = current_time

            time.sleep(1) # Sleep for a short interval to prevent busy-waiting

        self.logger.info(f"Agent {self.agent_name} shutting down.")
        # Any cleanup needed before shutdown
        self._flush_experience_buffer()
        # Deregister from hub (optional, could be handled by main_agent_entrypoint or hub)