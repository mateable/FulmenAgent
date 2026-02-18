# Swarm Mode Guide

Orchestrate multiple agents to collaborate on complex tasks. A lead agent decomposes a task, delegates sub-tasks to peer agents, collects results, and synthesizes a final answer.

---

## What Is Swarm Mode?

Swarm mode enables a group of agents to work together on a single complex task. Instead of one agent doing everything, the work is split across multiple agents operating in parallel.

```
         User sends complex task
                  │
                  ▼
        ┌──────────────────┐
        │   Lead Agent     │
        │                  │
        │  Decomposes task │
        │  into sub-tasks  │
        └──────┬───────────┘
               │
      ┌────────┼────────┐
      ▼        ▼        ▼
  ┌────────┐ ┌────────┐ ┌────────┐
  │Worker 1│ │Worker 2│ │Worker 3│
  │        │ │        │ │        │
  │Sub-task│ │Sub-task│ │Sub-task│
  │   A    │ │   B    │ │   C    │
  └───┬────┘ └───┬────┘ └───┬────┘
      │          │          │
      └──────────┼──────────┘
                 ▼
        ┌──────────────────┐
        │   Lead Agent     │
        │                  │
        │  Synthesizes all │
        │  results into    │
        │  final answer    │
        └──────────────────┘
                 │
                 ▼
           Final Result
```

---

## Quick Start

### Prerequisites

You need at least 2 agents running. The more agents, the more sub-tasks can run in parallel.

```bash
python3 main_agent_entrypoint.py \
  --num-agents 3 \
  --agent-names "Leader,Researcher,Analyst" \
  --connector-types "none,none,none" \
  --proactive-loops "no,no,no"
```

### Launch from Dashboard

1. Go to the **Swarm** tab in the dashboard
2. Select a **Lead Agent** from the dropdown (this agent will coordinate the work)
3. Enter a **Task** (e.g., "Research the top 5 programming languages for AI and compare their strengths")
4. Optionally check specific agents to include as workers (leave unchecked to use all available agents)
5. Click **Launch Swarm**

The swarm run appears in the **Active Swarm Runs** section with live status updates.

### Launch from Connectors (Discord / Telegram / Slack)

Prefix your message with `swarm:` in any connected chat:

```
swarm: Research the latest trends in renewable energy and summarize findings
```

The agent receiving the message becomes the lead agent and automatically launches a swarm run.

---

## How It Works

### Step-by-Step Flow

1. **Launch**: User triggers a swarm (dashboard or connector)
2. **Hub creates swarm run**: Assigns a unique run ID, records the task and lead agent
3. **Lead agent receives tagged message**: The hub sends the lead agent a special message:
   ```
   [SWARM:<run_id>] You are the LEAD AGENT for this swarm task.
   Your job: decompose this task and delegate sub-tasks to peer agents.

   TASK: <the user's task>

   Available peer agents: Worker1, Worker2, Worker3

   Instructions:
   - Break the task into specific sub-tasks
   - Use "send_agent_message" tool to delegate each sub-task to a peer agent
   - Each agent should be given a clear, specific task
   - After delegating, use "send_user_message" to confirm what was delegated
   ```
4. **Lead agent plans and delegates**: Using its LLM planner, the lead agent decomposes the task and uses the `send_agent_message` tool to send sub-tasks to peer agents
5. **Peer agents work**: Each peer agent processes its assigned sub-task independently
6. **Results collected**: When peer agents complete their work and send results via `send_user_message`, the hub automatically detects and collects results for the swarm run
7. **Aggregation**: Once all expected sub-results are in (or timeout), the hub sends an aggregation task to the lead agent with all collected results
8. **Final synthesis**: The lead agent combines all results into a final answer
9. **Completion**: The final result is displayed on the dashboard (and sent back through the connector if triggered from one)

### Swarm Run Statuses

| Status | Meaning |
|--------|---------|
| **running** | Swarm is active — agents are working |
| **collecting** | Sub-tasks delegated, waiting for results |
| **aggregating** | All results in, lead agent is synthesizing |
| **completed** | Final result is ready |
| **cancelled** | Manually cancelled by user |
| **failed** | An error occurred during execution |

---

## Dashboard UI

### Swarm Tab

The Swarm tab has three sections:

#### 1. Launch Swarm

Form to start a new swarm run:
- **Lead Agent**: Dropdown of active agents
- **Task**: Text input for the complex task
- **Select Agents**: Checkboxes to pick which agents participate (optional — defaults to all)
- **Launch Swarm** button

#### 2. Active Swarm Runs

Cards showing currently running swarms:
- Run ID, lead agent, status badge
- Task description
- Sub-task results as they come in (expandable)
- **Cancel** button to stop the swarm

#### 3. Completed Swarm Runs

Cards showing finished swarms:
- Final synthesized result (highlighted)
- All sub-task results (expandable)
- Timestamps (created, completed)
- **Delete** button to remove from history

---

## Agent-to-Agent Messaging

Swarm mode relies on the agent-to-agent messaging system. This is also available independently.

### How Agents Communicate

Agents use the `send_agent_message` tool to send messages to each other:

```
Tool: send_agent_message
Arguments:
  agent_name: "Researcher"
  message: "Research the top 3 Python web frameworks and their performance benchmarks"
```

The message is relayed through the hub and delivered to the target agent's message queue.

### Peer Agent Awareness

Each agent automatically discovers its peers when planning tasks. The planner's system prompt includes a live list of active agents:

```
Peer agents online (use "send_agent_message" to collaborate):
  Researcher, Analyst, Writer
  - You can delegate sub-tasks to these agents
  - Be specific about what you need each agent to do
```

### Messages Tab

The **Messages** tab on the dashboard shows all agent-to-agent message history:
- Filter by agent name
- See sender, recipient, message content, and timestamp
- Send messages between agents manually from the dashboard

---

## Connector Integration

Swarm works from any connected chat platform.

### Discord

```
swarm: Compare React, Vue, and Angular for enterprise applications
```

The Discord bot agent becomes the lead, launches the swarm, and sends the final result back to the Discord channel.

### Telegram

```
swarm: Analyze the pros and cons of microservices vs monolith architecture
```

Same flow — the Telegram bot agent leads the swarm and replies in the Telegram chat.

### Slack

```
swarm: Research the best CI/CD tools for a Python project
```

The Slack bot agent coordinates the swarm and posts the final result back to the Slack channel.

### How Connector Swarm Works

1. User sends a message starting with `swarm:` in any connected chat
2. The connector's agent detects the prefix in `handle_message()`
3. Agent calls `POST /api/swarm/launch` on the hub with itself as lead agent
4. A confirmation message is sent back to the chat: "Swarm launched! Task: ..."
5. When the swarm completes, the final result is routed back through the connector to the original chat

---

## API Reference

### Launch Swarm

```
POST /api/swarm/launch
Content-Type: application/json

{
  "task": "Research AI trends and summarize",
  "lead_agent": "Leader",
  "agents": ["Researcher", "Analyst"]  // optional, defaults to all active
}
```

**Response:**
```json
{
  "status": "success",
  "run_id": "swarm_abc123",
  "message": "Swarm launched"
}
```

### List Swarm Runs

```
GET /api/swarm/runs
```

**Response:**
```json
{
  "swarm_runs": [
    {
      "id": "swarm_abc123",
      "task": "Research AI trends",
      "lead_agent": "Leader",
      "status": "running",
      "agents": ["Researcher", "Analyst"],
      "sub_results": {},
      "final_result": null,
      "created_at": "2026-02-18T10:30:00",
      "completed_at": null
    }
  ]
}
```

### Cancel Swarm

```
POST /api/swarm/runs/<run_id>/cancel
```

### Delete Swarm Run

```
DELETE /api/swarm/runs/<run_id>
```

---

## Tips and Best Practices

- **Name agents by role**: Use descriptive names like "Researcher", "Analyst", "Writer" — the LLM uses these names when deciding how to delegate
- **Give clear tasks**: The more specific the swarm task, the better the lead agent can decompose it
- **3-5 agents is ideal**: Too few and there's nothing to parallelize; too many and coordination overhead increases
- **Monitor in dashboard**: Watch the Swarm tab for real-time progress on active runs
- **Use with workflows**: For repeatable multi-agent pipelines, consider using [Workflows](workflow-guide.md) instead — swarm is better for ad-hoc complex tasks

---

## Comparison: Swarm vs Workflows

| Feature | Swarm | Workflows |
|---------|-------|-----------|
| **Structure** | Dynamic — lead agent decides how to split work | Static — predefined step sequence |
| **Best for** | Ad-hoc complex tasks | Repeatable pipelines |
| **Agent assignment** | Lead agent delegates dynamically | Each step has a pre-assigned agent |
| **Data flow** | Results collected by hub, aggregated by lead | Template variables pass between steps |
| **Approval gates** | No | Yes |
| **Failure recovery** | Lead agent can re-delegate | On-failure step routing |
| **Trigger** | Dashboard, connectors (`swarm:` prefix) | Dashboard, API |

Use **swarm** when you want the AI to figure out how to split the work. Use **workflows** when you want explicit control over the pipeline.
