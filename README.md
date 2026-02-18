# FulMen Agent Network

This project implements a multi-agent network designed for autonomous task execution, learning, and collaboration. It features a central Hub for agent registration and experience sharing, and individual Agents capable of planning, executing tasks using various tools, and reflecting on their experiences.

## Features

*   **Multi-Agent System**: Launch multiple agents that can operate independently or interact via a central Hub.
*   **Dynamic Planning**: Agents use an LLM (from configurable providers) to dynamically plan tasks into a series of tool calls.
*   **Configurable LLM Providers**: Support for multiple Large Language Model providers including OpenRouter, Ollama (local), and Moonshot AI, with the ability to enable/disable each via environment variables or the dashboard.
*   **Enhanced Memory with Voyage AI**: Integrates Voyage AI embeddings with ChromaDB to provide agents with robust long-term semantic memory for experiences, improving recall and planning.
*   **Tool-Use**: Agents are equipped with a comprehensive suite of tools for interacting with the file system (read/write files, list directories), executing shell commands, fetching web content, performing image analysis and generation, and communicating.
*   **Experience Sharing**: Agents submit their experiences (steps, results, reflections) to the Hub, allowing for collective learning and tip distillation.
*   **Proactive Loops**: Agents can operate in a proactive mode, continuously processing messages and goals.
*   **Communication Connectors**: Support for integrating with external platforms like Discord, Telegram, LINE, WhatsApp, X (Twitter), and Voice (Twilio).
*   **Swarm Mode**: Orchestrate multiple agents on complex tasks — a lead agent decomposes the work, delegates to peers, and synthesizes the final result. Trigger from the dashboard or any connector with `swarm:` prefix.
*   **Agent-to-Agent Messaging**: Agents discover peers automatically and collaborate via `send_agent_message`. Full message history visible on the dashboard.
*   **Web Dashboard**: A real-time web dashboard to monitor active agents, distilled tips, recent experiences, manage agent configurations, launch swarms, view agent messages, and track activity logs.
*   **Extensible**: Easily add new tools or modify agent behaviors.

## Project Structure

*   `src/`: Contains the core components of an agent (Agent, Planner, Executor, Critic, Memory). The `Memory` module now includes Voyage AI and ChromaDB integration for vector memory.
*   `tools/`: Defines the various tools agents can use (file operations, shell commands, web interactions, image processing, communication).
*   `connectors/`: Modules for integrating with external communication platforms (Discord, Telegram, LINE, WhatsApp, X, Voice).
*   `hub.py`: The central server for managing agents, sharing experiences, and providing the dashboard. Now includes expanded configuration handling for new LLM and connector settings.
*   `main_agent_entrypoint.py`: The main script to launch and configure the agent network.
*   `configure_agent.py`: A CLI wizard to set up agent-specific configurations.
*   `check_openrouter_models.py`, `list_openrouter_models.py`: Utilities for OpenRouter model management.
*   `interact_with_agents.py`: A CLI tool for sending messages and commands to agents.
*   `.env.example`: A template for environment variables.

## Setup and Installation

### 1. Clone the repository

```bash
git clone https://github.com/mateable/FulmenAgent.git
cd FulmenAgent
```

### 2. Python Environment

It's highly recommended to use a Python virtual environment.

```bash
python3 -m venv venv
source venv/bin/activate # On Windows: .\venv\Scripts\activate
```

### 3. Install Python Dependencies

Install the required Python packages:

```bash
pip3 install -r requirements.txt
```

### 4. System Dependencies (Recommended)

For managing long-running processes in the background, especially on servers, `GNU Screen` is highly recommended.

*   **Debian/Ubuntu**:
    ```bash
    sudo apt update
    sudo apt install screen
    ```
*   **CentOS/RHEL/Fedora**:
    ```bash
    sudo yum install screen
    # or for newer Fedora/RHEL
    sudo dnf install screen
    ```
    You can start a new screen session with `screen`, run your Python script, detach using `Ctrl+A D`, and reattach later with `screen -r`.

### 5. Configuration (`.env` file)

Create a `.env` file in the root directory of the project (e.g., by copying `.env.example` to `.env`).

```bash
cp .env.example .env
```

Edit the `.env` file and fill in the necessary environment variables. Sensitive keys can also be managed via the Web Dashboard.

#### General Settings:
*   **`HUB_URL`**: The URL where the central Hub will run (default: `http://127.0.0.1:5000`).
*   **`DEFAULT_PROACTIVE_LOOPS`**: `yes` or `no`. Sets the default for agents running proactive loops.
*   **`DEFAULT_EXECUTION_MODE`**: `safe` or `unrestricted`. Controls tool execution safety.
*   **`DEFAULT_BATCH_EXPERIENCE`**: `yes` or `no`. Determines how experiences are sent to the hub.
*   **`DEFAULT_PROACTIVE_INTERVAL`**: Interval in seconds for proactive loops.

#### LLM Provider Settings:
*   **`ENABLE_MOONSHOT_AI`**: `yes` or `no`. Enable/disable Moonshot AI (Kimi) as an LLM provider (default: `yes`).
*   **`ENABLE_OLLAMA`**: `yes` or `no`. Enable/disable Ollama (local LLMs) as an LLM provider (default: `yes`).
*   **`ENABLE_OPENROUTER`**: `yes` or `no`. Enable/disable OpenRouter as an LLM provider (default: `yes`).

*   **`OPENROUTER_API_KEY`**: Your API key for OpenRouter.
*   **`OPENROUTER_MODEL`**: The OpenRouter model ID you wish to use (e.g., `openai/gpt-3.5-turbo`). Use `python3 list_openrouter_models.py` to see available models.
*   **`OLLAMA_BASE_URL`**: URL for your local Ollama instance (e.g., `http://localhost:11434`).
*   **`OLLAMA_MODEL`**: Specific Ollama model (e.g., `llama3:latest`).
*   **`MOONSHOT_API_KEY`**: Your API key for Moonshot AI (Kimi).
*   **`MOONSHOT_MODEL`**: Specific Moonshot AI model (e.g., `kimi-k2.5`).

#### Voyage AI Settings (for Semantic Memory):
*   **`ENABLE_VOYAGE_AI`**: `yes` or `no`. Enable/disable Voyage AI for semantic memory (default: `no`).
*   **`VOYAGE_AI_API_KEY`**: Your API key for Voyage AI.

#### Communication Connector Settings:

*   **Discord Connector:**
    *   `DISCORDAGENT_DISCORD_TOKEN`
    *   `DISCORDAGENT_DISCORD_CHANNEL_ID`
*   **Telegram Connector:**
    *   `TELEGRAMAGENT_TELEGRAM_TOKEN`
    *   `TELEGRAMAGENT_TELEGRAM_ALLOWED_CHATS` (comma-separated IDs)
*   **LINE Connector:**
    *   `LINEAGENT_LINE_CHANNEL_SECRET`
    *   `LINEAGENT_LINE_CHANNEL_ACCESS_TOKEN`
*   **WhatsApp Connector:**
    *   `WHATSAPP_PHONE_NUMBER_ID`
    *   `WHATSAPP_ACCESS_TOKEN`
*   **X (Twitter) Connector:**
    *   `X_CONSUMER_KEY`
    *   `X_CONSUMER_SECRET`
    *   `X_ACCESS_TOKEN`
    *   `X_ACCESS_TOKEN_SECRET`
*   **Voice (Twilio) Connector:**
    *   `TWILIO_ACCOUNT_SID`
    *   `TWILIO_AUTH_TOKEN`
    *   `TWILIO_PHONE_NUMBER` (your Twilio phone number)
    *   `GOOGLE_APPLICATION_CREDENTIALS` (path to Google service account key for TTS/STT)

#### Interactive Configuration
Alternatively, you can use the interactive configuration wizard for basic settings:

```bash
python3 configure_agent.py
```

## Running the Agent Network

### 1. Start the Hub

The `main_agent_entrypoint.py` script will automatically try to start the Hub if it's not already running.

### 2. Launch Agents

Run the `main_agent_entrypoint.py` script to launch and configure agents:

```bash
python3 main_agent_entrypoint.py
```

This will guide you through setting up agent names, connectors, and whether they should run proactive loops.

You can also launch agents with command-line arguments:

```bash
python3 main_agent_entrypoint.py --num-agents 2 --agent-names AgentAlice,AgentBob --connector-types discord,none --proactive-loops yes,no
```

### 3. Access the Dashboard

Once the Hub is running, you can access the web dashboard to monitor the network and manage configurations:

[http://127.0.0.1:5000/dashboard](http://127.0.0.1:5000/dashboard)

## Interacting with Agents

You can send messages and commands to active agents using the `interact_with_agents.py` script:

```bash
python3 interact_with_agents.py
```

This will present an interactive menu. You can also use command-line arguments:

```bash
python3 interact_with_agents.py --choice 2 --agent-name AgentAlice --message "Please analyze the file 'src/agent.py' for potential improvements."
```

## Logging and Debugging

Agent activities, including their planning, tool executions, and results, are extensively logged.

*   Individual agent actions are logged to `agent_debug.log` in the root directory.
*   Hub activities are logged to `hub_stdout.log` and `hub_stderr.log` (if started by `main_agent_entrypoint.py`).
*   The Hub's web dashboard provides a view of recent agent experiences, distilled tips, and configurable settings.

The `WriteFileTool` uses a temporary `.tmp` file during its atomic write process for data integrity, but this file is transient and removed upon successful write. The primary mechanism for tracking agent actions is through the logs and the Hub's experience memory.

## Running the Voice Agent (Twilio)

To try out the voice scheduling and cold-calling capabilities for real, you'll need to connect the agent to a real phone number.

### Prerequisites

1.  **Twilio Account**: You need an active Twilio account with a phone number. From your account dashboard, you will need:
    *   `Account SID`
    *   `Auth Token`
    *   Your Twilio Phone Number
2.  **Google Cloud Credentials**: The agent uses Google Cloud for Text-to-Speech and Speech-to-Text. You will need a Google Cloud project with the required APIs enabled and a service account key file.
3.  **LLM Provider API Key**: An API key for your chosen LLM provider (e.g., OpenRouter, Moonshot AI) is required for the agent's language model.
4.  **ngrok**: Twilio needs to send webhooks to your local machine, so you need a tool to create a secure public URL. You can download and install it from [ngrok.com](https://ngrok.com/download).

### Setup and Execution

**Step 1: Set Up Your Environment Variables**

Create or edit your `.env` file in the root of the project directory and add your credentials (or configure via the dashboard):

```
# .env file

# For Voice (Twilio) Connector
TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TWILIO_AUTH_TOKEN="your_twilio_auth_token"
TWILIO_PHONE_NUMBER="+15551234567" # Your Twilio number

# For Google Cloud TTS and STT
GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/google-service-account-key.json"

# For Agent's Planner (example with OpenRouter)
OPENROUTER_API_KEY="sk-or-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
# ENABLE_OPENROUTER="yes" # Ensure it's enabled if using

# For Voyage AI Memory (example)
# VOYAGE_AI_API_KEY="voyage-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
# ENABLE_VOYAGE_AI="yes"
```

**Step 2: Expose Your Local Server with ngrok**

The `VoiceConnector` runs on port 5002 by default. Open a new terminal and run:

```bash
ngrok http 5002
```

`ngrok` will show you a "Forwarding" URL. Copy the `https` URL (e.g., `https://<random-string>.ngrok-free.app`).

**Step 3: Configure Your Twilio Number**

1.  Go to the "Active Numbers" section of your Twilio console.
2.  Click on the phone number you want to use.
3.  Scroll down to the "Voice & Fax" section.
4.  Under **"A CALL COMES IN"**, set the webhook to your `ngrok` forwarding URL, adding `/twilio-webhook` at the end (e.g., `https://<random-string>.ngrok-free.app/twilio-webhook`).
5.  Make sure the method is set to `HTTP POST`.
6.  Click **"Save"**.

**Step 4: Run the Agent**

In your project terminal, run the following command to start the agent with the voice connector:

```bash
python3 main_agent_entrypoint.py --agent-names VoiceAgent --connector-types voice
```

**Step 5: Make a Call**

Call your Twilio phone number from your actual phone. You should hear the agent greet you. You can then try giving it a command, like "schedule a meeting for tomorrow at 4pm".

### Cold Calling Feature

The agent is also equipped with a `cold_call` tool that allows it to initiate outbound calls.

**How it Works:**
The `cold_call` tool uses several other tools in sequence:
1.  **`SynthesizeSpeechTool`**: Converts a `call_script` (text) into an audio file using Google Cloud TTS.
2.  **`MakePhoneCallTool`**: Simulates placing an outbound call to a specified `phone_number`. In a real-world scenario with a configured VoIP provider, this would make a real call.
3.  **`TranscribeVoiceTool`**: Simulates listening for a response and transcribing it using Google Cloud STT.

**How to Use:**
To use this feature, you need to give the agent a goal that would require it to make a call. You can do this via the `interact_with_agents.py` script.

Example command to the agent:
```
"Use the cold_call tool to call the number +15559876543, introduce yourself as an AI assistant, and ask if they are interested in learning about our new product. The contact's name is Jane Doe."
```

The agent's planner will interpret this goal, identify the `cold_call` tool, extract the necessary arguments (`phone_number`, `contact_name`, `call_script`), and execute the tool. The same `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `GOOGLE_APPLICATION_CREDENTIALS` will be used for these actions.

## Swarm Mode (Multi-Agent Collaboration)

Swarm mode lets multiple agents collaborate on complex tasks. A lead agent decomposes the task, delegates sub-tasks to peer agents, collects their results, and synthesizes a final answer.

### How It Works

1.  **Launch a swarm** from the dashboard **Swarm** tab or from any connector by prefixing your message with `swarm:`
2.  The **lead agent** receives the task, plans how to decompose it, and delegates sub-tasks to peer agents using `send_agent_message`
3.  **Peer agents** work on their sub-tasks independently
4.  The **hub collects results** as agents complete their work
5.  Once all results are in, the **lead agent synthesizes** a final answer

### Launch from Dashboard

1.  Go to the **Swarm** tab
2.  Select a **Lead Agent** and enter a **Task**
3.  Optionally select specific worker agents
4.  Click **Launch Swarm**

### Launch from Connectors (Discord / Telegram / Slack)

Prefix your message with `swarm:` in any connected chat:

```
swarm: Research the top 5 AI frameworks and compare their performance
```

The bot agent becomes the lead, runs the swarm, and sends the final result back to the chat.

### Agent-to-Agent Messaging

Agents automatically discover their peers and can message each other using the `send_agent_message` tool. The **Messages** tab on the dashboard shows all inter-agent communication history.

### Quick Start

```bash
# Launch 3 agents for swarm collaboration
python3 main_agent_entrypoint.py \
  --num-agents 3 \
  --agent-names "Leader,Researcher,Analyst" \
  --connector-types "none,none,none"
```

For full details, see the [Swarm Mode Guide](docs/swarm-mode-guide.md).

## Disclaimer

This `FulMen Agent Network` project is open-source and provided "as-is" without any warranty, express or implied. The software is under active development and may contain bugs or unexpected behavior.

**Your Responsibilities:**
*   **Costs**: You are solely responsible for any costs incurred through the use of external services (e.g., API calls to OpenRouter, Twilio, Google Cloud TTS/STT, Voyage AI). These costs can accumulate quickly with autonomous agents.
*   **Legal Compliance**: You are responsible for ensuring your use of this software complies with all applicable laws and regulations, especially concerning automated communication (e.g., telemarketing laws, data privacy regulations like GDPR, CCPA).
*   **Ethical Use**: Be mindful of the ethical implications of deploying autonomous agents. Ensure your use cases are transparent, fair, and do not cause harm.
*   **Security**: Always review the code, especially before integrating it with sensitive systems or data. Do not expose API keys or other credentials publicly.

By using this software, you agree to assume all risks associated with its use. The developers and contributors shall not be liable for any damages or liabilities arising from your use of this software.