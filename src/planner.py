from typing import List, Dict, Any
from agent_network.src.memory import Memory
import os
import json
import logging
import requests # Re-add requests
import time # Re-add time
import random # Re-add random
from ollama import Client # Re-add Ollama Client
from openai import OpenAI # Re-add OpenAI for OpenRouter/Moonshot

class Planner:
    def __init__(self, agent_name: str, base_model: Any, memory: Memory, tools: List[Any], llm_provider_settings: Dict[str, Any], hub_url: str = None):
        self.agent_name = agent_name
        self.base_model = base_model
        self.memory = memory
        self.tools = {tool.name: tool for tool in tools}
        self.llm_provider_settings = llm_provider_settings
        self.hub_url = hub_url or os.environ.get("HUB_URL", "http://127.0.0.1:5000")
        self.logger = logging.getLogger(f"Planner.{agent_name}")

        self.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
        self.openrouter_chat_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions") # Use base_url from env or default
        self.openrouter_models_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1") + "/models"
        self.free_models = [] # To store a list of available free OpenRouter models
        self.blacklisted_models = set() # To store OpenRouter models that hit daily rate limits

        self.ollama_base_url = os.environ.get("OLLAMA_BASE_URL")
        self.ollama_model = os.environ.get("OLLAMA_MODEL") # Specific Ollama model chosen by user
        self.ollama_client = None
        self.available_ollama_models = [] # To store a list of available Ollama models

        self.moonshot_api_key = os.environ.get("MOONSHOT_API_KEY")
        self.moonshot_model = os.environ.get("MOONSHOT_MODEL") # Specific Kimi model chosen by user
        self.moonshot_client = None # Placeholder for Moonshot OpenAI client
        self.available_moonshot_models = [] # To store a list of available Kimi models (not actively fetched but for consistency)

        self.huggingface_api_key = os.environ.get("HUGGINGFACE_API_KEY")
        self.huggingface_model = os.environ.get("HUGGINGFACE_MODEL")
        self.huggingface_client = None
        self.available_huggingface_models = []

        # Initialize LLM providers
        self._initialize_llm_providers()

    def update_tools(self, new_tools: List[Any]):
        """
        Updates the planner's internal tools dictionary.
        This is called after initial tools and plugin tools are loaded.
        """
        self.tools = {tool.name: tool for tool in new_tools}
        self.logger.debug(f"Planner tools updated. Available tools: {list(self.tools.keys())}")

    def _initialize_llm_providers(self):
        """Initializes LLM provider clients (OpenRouter, Ollama, Moonshot)."""
        # Log initial LLM provider status
        self.logger.info(f"DEBUG: Planner initialized. "
                         f"OPENROUTER_API_KEY: {'SET' if self.openrouter_api_key else 'NOT SET'}. "
                         f"OLLAMA_BASE_URL: {'SET' if self.ollama_base_url else 'NOT SET'}. "
                         f"MOONSHOT_API_KEY: {'SET' if self.moonshot_api_key else 'NOT SET'}. "
                         f"HUGGINGFACE_API_KEY: {'SET' if self.huggingface_api_key else 'NOT SET'}. "
                         f"ENABLE_MOONSHOT_AI: {self.llm_provider_settings.get('ENABLE_MOONSHOT_AI')}. "
                         f"ENABLE_OLLAMA: {self.llm_provider_settings.get('ENABLE_OLLAMA')}. "
                         f"ENABLE_OPENROUTER: {self.llm_provider_settings.get('ENABLE_OPENROUTER')}. "
                         f"ENABLE_HUGGINGFACE: {self.llm_provider_settings.get('ENABLE_HUGGINGFACE')}.")

        # Initialize OpenRouter models if enabled
        if self.llm_provider_settings.get("ENABLE_OPENROUTER") == "yes" and self.openrouter_api_key:
            self.free_models = self._get_available_openrouter_models(free_only=True)
            if not self.free_models:
                self.logger.warning("No free OpenRouter models found. Planner might rely on other providers.")
            else:
                self.logger.info(f"Found {len(self.free_models)} free OpenRouter models.")
        elif self.llm_provider_settings.get("ENABLE_OPENROUTER") == "yes" and not self.openrouter_api_key:
            self.logger.warning("OPENROUTER_API_KEY not set, OpenRouter will not be used.")

        # Initialize Ollama client and models if enabled
        if self.llm_provider_settings.get("ENABLE_OLLAMA") == "yes" and self.ollama_base_url:
            try:
                self.ollama_client = Client(host=self.ollama_base_url)
                self.available_ollama_models = self._get_available_ollama_models()
                if not self.available_ollama_models:
                    self.logger.warning(f"No models found on Ollama server at {self.ollama_base_url}.")
                    self.ollama_client = None # Disable Ollama if no models
                else:
                    self.logger.info(f"Found {len(self.available_ollama_models)} models on Ollama server at {self.ollama_base_url}.")
                    if not self.ollama_model and self.available_ollama_models:
                        # If no specific Ollama model is chosen, default to the first one available
                        self.ollama_model = self.available_ollama_models[0].model_dump().get('name')
                        self.logger.info(f"No specific OLLAMA_MODEL set. Defaulting to first available: {self.ollama_model}")
                    self.logger.info(f"Ollama model selected: {self.ollama_model}")
            except Exception as e:
                self.logger.error(f"Error initializing Ollama client at {self.ollama_base_url}: {e}")
                self.ollama_client = None
        elif self.llm_provider_settings.get("ENABLE_OLLAMA") == "yes" and not self.ollama_base_url:
            self.logger.warning("OLLAMA_BASE_URL not set, Ollama will not be used.")

        # Initialize Moonshot client if enabled
        if self.llm_provider_settings.get("ENABLE_MOONSHOT_AI") == "yes" and self.moonshot_api_key:
            self.moonshot_client = OpenAI(
                api_key=self.moonshot_api_key,
                base_url=os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
            )
        elif self.llm_provider_settings.get("ENABLE_MOONSHOT_AI") == "yes" and not self.moonshot_api_key:
            self.logger.warning("MOONSHOT_API_KEY not set, Moonshot AI will not be used.")

        # Initialize HuggingFace client if enabled
        if self.llm_provider_settings.get("ENABLE_HUGGINGFACE") == "yes" and self.huggingface_api_key:
            self.huggingface_client = OpenAI(
                api_key=self.huggingface_api_key,
                base_url=os.environ.get("HUGGINGFACE_BASE_URL", "https://api-inference.huggingface.co/v1")
            )
        elif self.llm_provider_settings.get("ENABLE_HUGGINGFACE") == "yes" and not self.huggingface_api_key:
            self.logger.warning("HUGGINGFACE_API_KEY not set, HuggingFace will not be used.")

    def _get_available_openrouter_models(self, free_only: bool = True) -> List[str]:
        headers = {"Authorization": f"Bearer {self.openrouter_api_key}"}
        try:
            response = requests.get(self.openrouter_models_url, headers=headers)
            response.raise_for_status()
            models = response.json().get('data', [])
            available_models = [
                m['id'] for m in models
                if (not free_only or m.get('pricing', {}).get('prompt', 0) == 0) and m['id'] not in self.blacklisted_models
            ]
            return available_models
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching OpenRouter models: {e}")
            return []

    def _get_available_ollama_models(self) -> List[Dict[str, Any]]:
        if not self.ollama_client:
            return []
        try:
            response = self.ollama_client.list()
            return response.get('models', [])
        except Exception as e:
            self.logger.error(f"Error fetching Ollama models: {e}")
            return []

    def _call_llm(self, prompt: str, temperature: float = 0.7, provider: str = "openrouter", model: str = None) -> Dict[str, Any]:
        """
        Calls the appropriate LLM based on the provider settings.
        Returns the content of the LLM's response and a dictionary of token usage.
        """
        token_usage = {"provider": provider, "prompt_tokens": 0, "completion_tokens": 0}
        
        try:
            if provider == "openrouter" and self.llm_provider_settings.get("ENABLE_OPENROUTER") == "yes" and self.openrouter_api_key:
                # Ensure the OpenAI client is initialized correctly with base_url and api_key from self.
                client = OpenAI(
                    base_url=self.openrouter_chat_url.replace("/chat/completions", ""), # Extract base URL from chat_url
                    api_key=self.openrouter_api_key
                )
                used_model = model if model else (os.environ.get("OPENROUTER_MODEL") or self.base_model) # Use agent's base_model as ultimate fallback
                if not used_model:
                    self.logger.warning(f"No OpenRouter model specified for _call_llm.")
                    return {"content": "Error: No OpenRouter model specified.", "token_usage": token_usage}

                chat_completion = client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=used_model,
                    temperature=temperature,
                    stream=False
                )
                if chat_completion.usage:
                    token_usage["prompt_tokens"] = chat_completion.usage.prompt_tokens
                    token_usage["completion_tokens"] = chat_completion.usage.completion_tokens
                return {"content": chat_completion.choices[0].message.content, "token_usage": token_usage}

            elif provider == "ollama" and self.llm_provider_settings.get("ENABLE_OLLAMA") == "yes" and self.ollama_client:
                used_model = model if model else (self.ollama_model or self.base_model) # Use agent's base_model as ultimate fallback
                if not used_model:
                    self.logger.warning(f"No Ollama model specified for _call_llm.")
                    return {"content": "Error: No Ollama model specified.", "token_usage": token_usage}

                self.logger.debug(f"Calling Ollama chat with model: {used_model}, prompt: {prompt[:200]}...") # New debug log
                
                try:
                    ollama_response = self.ollama_client.chat( # Use self.ollama_client
                        model=used_model,
                        messages=[{"role": "user", "content": prompt}],
                        format="json",  # Force Ollama to output valid JSON
                        options={"temperature": temperature}
                    )
                    self.logger.debug(f"Received raw Ollama response: {ollama_response}") # Log raw response
                except Exception as ollama_e:
                    self.logger.error(f"Error during ollama_client.chat call: {ollama_e}")
                    return {"content": f"Error communicating with Ollama: {ollama_e}", "token_usage": token_usage}

                # Ollama's API response doesn't directly provide token usage like OpenAI's.
                # You might need to estimate or implement a tokenizer if precise tracking is needed.
                # For now, we'll return 0 tokens for Ollama.
                return {"content": ollama_response["message"]["content"], "token_usage": token_usage}

            elif provider == "moonshot" and self.llm_provider_settings.get("ENABLE_MOONSHOT_AI") == "yes" and self.moonshot_client:
                used_model = model if model else (self.moonshot_model or self.base_model) # Use agent's base_model as ultimate fallback
                if not used_model:
                    self.logger.warning(f"No Moonshot AI model specified for _call_llm.")
                    return {"content": "Error: No Moonshot AI model specified.", "token_usage": token_usage}

                chat_completion = self.moonshot_client.chat.completions.create( # Use self.moonshot_client
                    messages=[{"role": "user", "content": prompt}],
                    model=used_model,
                    temperature=temperature,
                    stream=False
                )
                if chat_completion.usage:
                    token_usage["prompt_tokens"] = chat_completion.usage.prompt_tokens
                    token_usage["completion_tokens"] = chat_completion.usage.completion_tokens
                return {"content": chat_completion.choices[0].message.content, "token_usage": token_usage}

            elif provider == "huggingface" and self.llm_provider_settings.get("ENABLE_HUGGINGFACE") == "yes" and self.huggingface_client:
                used_model = model if model else (self.huggingface_model or self.base_model)
                if not used_model:
                    self.logger.warning(f"No HuggingFace model specified for _call_llm.")
                    return {"content": "Error: No HuggingFace model specified.", "token_usage": token_usage}

                chat_completion = self.huggingface_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=used_model,
                    temperature=temperature,
                    stream=False
                )
                if chat_completion.usage:
                    token_usage["prompt_tokens"] = chat_completion.usage.prompt_tokens
                    token_usage["completion_tokens"] = chat_completion.usage.completion_tokens
                return {"content": chat_completion.choices[0].message.content, "token_usage": token_usage}

            else:
                self.logger.warning(f"LLM provider '{provider}' is either not enabled, not configured, or not supported.")
                return {"content": f"Error: LLM provider '{provider}' is not enabled, not configured, or not supported.", "token_usage": token_usage}

        except Exception as e:
            self.logger.error(f"Error calling LLM with provider '{provider}': {e}")
            return {"content": f"Error calling LLM: {e}", "token_usage": token_usage}

    def evaluate_prompt(self, prompt: str) -> Dict[str, Any]:
        """
        Evaluates a prompt using the LLM and returns the response content and token usage.
        """
        # Default to OpenRouter if enabled, otherwise try Ollama, then Moonshot, then HuggingFace
        provider = "openrouter"
        if self.llm_provider_settings.get("ENABLE_OPENROUTER") != "yes":
            if self.llm_provider_settings.get("ENABLE_OLLAMA") == "yes":
                provider = "ollama"
            elif self.llm_provider_settings.get("ENABLE_MOONSHOT_AI") == "yes":
                provider = "moonshot"
            elif self.llm_provider_settings.get("ENABLE_HUGGINGFACE") == "yes":
                provider = "huggingface"
            else:
                self.logger.warning("No LLM provider is enabled for evaluation.")
                return {"content": "No LLM provider enabled.", "token_usage": {"provider": "none", "prompt_tokens": 0, "completion_tokens": 0}}
        
        self.logger.debug(f"Calling _call_llm from evaluate_prompt with provider: {provider}, prompt: {prompt[:100]}...") # NEW DEBUG
        response = self._call_llm(prompt, temperature=0.5, provider=provider)
        return response # Returns {"content": ..., "token_usage": {...}}

    def _get_peer_agents_section(self) -> str:
        """Fetch active agents from the hub and build a peer agents section for the prompt."""
        try:
            resp = requests.get(f"{self.hub_url}/get_active_agents", timeout=3)
            resp.raise_for_status()
            agents_data = resp.json()
            peer_names = [name for name in agents_data.keys() if name != self.agent_name]
            if peer_names:
                peer_list = ", ".join(peer_names)
                return f"""
Peer agents online (use "send_agent_message" to collaborate):
  {peer_list}
  - You can delegate sub-tasks to these agents. Use send_agent_message with args: {{"agent_name": "<name>", "message": "<what you need them to do>"}}
"""
            return ""
        except Exception:
            return ""

    def create_plan(self, task: str, recent_experiences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Creates a plan to address the given task, incorporating recent experiences and relevant memories.
        Returns the plan (as a string) and aggregated token usage.
        """
        aggregated_token_usage = {"moonshot_ai": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "ollama": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "openrouter": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "voyage_ai": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "huggingface": {"prompt_tokens": 0, "completion_tokens": 0}}

        relevant_memories = []
        if self.llm_provider_settings.get("ENABLE_VOYAGE_AI") == "yes":
            try:
                # Retrieve relevant memories using Voyage AI embeddings
                self.logger.info(f"Retrieving relevant memories for task: {task}")
                retrieved_docs = self.memory.retrieve_relevant_memories(query=task, n_results=5)
                for doc in retrieved_docs:
                    relevant_memories.append(doc)
                
                # Token usage for embedding model is handled internally by Memory, 
                # but for planning LLM call, we still track.
            except Exception as e:
                self.logger.error(f"Error retrieving relevant memories with Voyage AI: {e}")
                relevant_memories = ["Error retrieving memories."]
        else:
            self.logger.info("Voyage AI is not enabled for memory retrieval.")

        self.logger.debug(f"Relevant memories before joining: {relevant_memories}") # NEW DEBUG

        memories_str = "\n".join(relevant_memories) if relevant_memories else "No relevant memories found."
        
        self.logger.debug(f"Available tools for planning: {list(self.tools.keys())}") # NEW DEBUG

        # Build tool descriptions so the LLM knows what each tool does and how to call it
        tool_descriptions = "\n".join([f"  - {tool.name}: {tool.description}" for tool in self.tools.values()])

        # Fetch peer agents for collaboration awareness
        peer_agents_section = self._get_peer_agents_section()

        # Detect AI-to-AI compact mode
        ai2ai_mode = False
        ai2ai_sender = ""
        clean_task = task
        if task.startswith("[AI2AI:"):
            ai2ai_mode = True
            closing = task.index("]")
            ai2ai_sender = task[7:closing]
            clean_task = task[closing+2:]  # Strip the tag
            self.logger.info(f"[GibberLink] Compact planning mode for AI sender: {ai2ai_sender}")

        compact_rules = ""
        if ai2ai_mode:
            compact_rules = """
- COMPACT MODE: You are talking to another AI agent. Save tokens:
  - Use shorthand in all outputs: "temp:45.2F|sky:ptly_cld|hum:78%" not full sentences.
  - Use gibberlink_send to reply (auto-compresses). Target: """ + ai2ai_sender + """
  - Keep tool args minimal. No filler words.
  - If using send_user_message or gibberlink_send, keep the message under 50 words using key:value pairs."""

        prompt = f"""You are "{self.agent_name}", an AI agent in a multi-agent network. Create a plan for this task.

TASK: {clean_task}

Available tools:
{tool_descriptions}
{peer_agents_section}
RULES:
- Create only 1 step for simple tasks.
- For weather tasks, use "weather_tool" with the EXACT location from the task.
- If the task is complex, consider delegating sub-tasks to peer agents using "send_agent_message".
- When delegating, be specific about what you need the other agent to do.
- Respond with ONLY a JSON object.{compact_rules}

Example response:
{{
    "plan": [
        {{"goal": "Get current temperature for {clean_task}", "tool": "weather_tool", "args": {{"action": {{"type": "current_temp", "location": "{clean_task}"}}}}}}
    ]
}}"""
        
        response = self.evaluate_prompt(prompt)
        plan_content = response["content"]
        
        # Aggregate token usage from the planning LLM call
        if response["token_usage"]["provider"] != "none":
            provider = response["token_usage"]["provider"]
            if provider in aggregated_token_usage:
                aggregated_token_usage[provider]["prompt_tokens"] += response["token_usage"]["prompt_tokens"]
                aggregated_token_usage[provider]["completion_tokens"] += response["token_usage"]["completion_tokens"]
            else: # Should not happen if `aggregated_token_usage` is pre-populated
                aggregated_token_usage[provider] = response["token_usage"]

        # Strip markdown code fences if the LLM wrapped the JSON in them
        stripped_content = plan_content.strip()
        if stripped_content.startswith("```"):
            # Remove opening fence (e.g. ```json or ```)
            stripped_content = stripped_content.split("\n", 1)[1] if "\n" in stripped_content else stripped_content[3:]
            # Remove closing fence
            if stripped_content.endswith("```"):
                stripped_content = stripped_content[:-3].strip()

        try:
            plan = json.loads(stripped_content)
            return {"plan": plan["plan"], "token_usage": aggregated_token_usage}
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode plan JSON: {plan_content}")
            return {"plan": [{"goal": f"Error: Could not parse plan from LLM. Raw response: {plan_content}", "tool": "None", "args": {}}], "token_usage": aggregated_token_usage}

    def reflect(self, task: str, experiences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Reflects on the task and experiences to generate feedback and distilled tips.
        Returns reflection (as a string) and aggregated token usage.
        """
        aggregated_token_usage = {"moonshot_ai": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "ollama": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "openrouter": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "voyage_ai": {"prompt_tokens": 0, "completion_tokens": 0},
                                  "huggingface": {"prompt_tokens": 0, "completion_tokens": 0}}
        
        experiences_str = "\n".join([json.dumps(exp) for exp in experiences])
        
        prompt = f"""
        You are an AI agent designed to reflect on your performance after attempting a task.
        Here is the original task: {task}
        Here are the experiences (steps taken and their results):
        {experiences_str}
        
        Based on these, provide constructive feedback on your performance and distill any valuable tips or lessons learned.
        Your response should be a JSON object with two keys: "feedback" (a string) and "distilled_tips" (a list of strings).
        Example:
        {{
            "feedback": "The initial search was too broad...",
            "distilled_tips": ["Always refine search queries...", "Consider edge cases..."]
        }}
        """
        response = self.evaluate_prompt(prompt)
        reflection_content = response["content"]

        # Aggregate token usage from the reflection LLM call
        if response["token_usage"]["provider"] != "none":
            provider = response["token_usage"]["provider"]
            if provider in aggregated_token_usage:
                aggregated_token_usage[provider]["prompt_tokens"] += response["token_usage"]["prompt_tokens"]
                aggregated_token_usage[provider]["completion_tokens"] += response["token_usage"]["completion_tokens"]
            else: # Should not happen if `aggregated_token_usage` is pre-populated
                aggregated_token_usage[provider] = response["token_usage"]

        # Strip markdown code fences if the LLM wrapped the JSON in them
        stripped_reflection = reflection_content.strip()
        if stripped_reflection.startswith("```"):
            stripped_reflection = stripped_reflection.split("\n", 1)[1] if "\n" in stripped_reflection else stripped_reflection[3:]
            if stripped_reflection.endswith("```"):
                stripped_reflection = stripped_reflection[:-3].strip()

        try:
            reflection = json.loads(stripped_reflection)
            return {"reflection": reflection, "token_usage": aggregated_token_usage}
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode reflection JSON: {reflection_content}")
            return {"reflection": {"feedback": f"Error: Could not parse reflection from LLM. Raw response: {reflection_content}", "distilled_tips": []}, "token_usage": aggregated_token_usage}
