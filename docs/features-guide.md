# Dashboard Features Guide

Reference for the built-in dashboard features: Agent Templates, Webhooks, Notifications, Broadcast, and Scheduled Tasks.

---

## Agent Templates

Save and reuse agent launch configurations so you don't have to re-enter the same settings every time.

### Saving a Template

1. Go to the **Dashboard** tab
2. Fill out the **Add New Agent** form with your desired settings:
   - Agent Name, Connector Type, Proactive Loop, Execution Mode
   - Batch Experience, Proactive Interval
   - LLM Provider Override (optional — API key + model)
   - Initial Goal (optional)
3. Click **"Save as Template"** (next to the Launch button)
4. Enter a name for the template (e.g., "My OpenRouter Agent")
5. The template is saved to `agent_templates.json` and appears in the dropdown

### Loading a Template

1. At the top of the Add New Agent form, open the **"Load Template"** dropdown
2. Select a saved template
3. All form fields auto-populate with the saved values
4. Adjust anything if needed, then click **"Launch New Agent"**

### Deleting a Template

1. Select the template in the dropdown
2. Click **"Delete Template"**

### What Gets Saved

| Field | Saved? |
|-------|--------|
| Agent Name | Yes |
| Connector Type | Yes |
| Proactive Loop | Yes |
| Execution Mode | Yes |
| Batch Experience | Yes |
| Proactive Interval | Yes |
| LLM Provider Override | Yes |
| LLM Model | Yes |
| Initial Goal | Yes |
| LLM API Key | **No** (not stored for security) |

> **Note:** API keys are never saved in templates. If your template uses a per-agent LLM provider override, you'll need to re-enter the API key each time you load the template.

---

## Webhooks

Let external services trigger agent tasks by sending an HTTP POST request to a unique URL.

### Creating a Webhook

1. Go to the **Webhooks** tab
2. Enter a **name** (e.g., "GitHub Push") and select the **target agent**
3. Click **"Create Webhook"**
4. A unique URL with a secret token appears in the table — click **Copy** to grab it

### Triggering a Webhook

Send a POST request to the webhook URL:

```bash
curl -X POST "http://your-hub:5000/webhook/<id>?secret=<token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "New commit pushed to main branch"}'
```

The message is delivered to the target agent's queue with `sender: "webhook"`.

You can also pass the secret via the `X-Webhook-Secret` header instead of a query parameter:

```bash
curl -X POST "http://your-hub:5000/webhook/<id>" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <token>" \
  -d '{"message": "Deploy triggered"}'
```

If no `message` field is provided in the body, the entire JSON payload is forwarded as context.

### Webhook Table

| Column | Description |
|--------|-------------|
| Name | The webhook's display name |
| Agent | Which agent receives the message |
| Webhook URL | The full URL with secret (click Copy) |
| Triggers | How many times this webhook has been fired |
| Last Triggered | When it was last fired |
| Enabled | Toggle on/off without deleting |

### Use Cases

- **GitHub**: Trigger an agent when a push, PR, or issue event occurs
- **Zapier / IFTTT**: Connect any app to your agents via webhook
- **Cron jobs**: Use system cron + curl to trigger agents on custom schedules
- **CI/CD**: Notify an agent when a build completes

---

## Notifications

A notification bell in the dashboard header shows real-time events from the hub.

### How It Works

- Click the **bell icon** (top-right area) to open the notification panel
- A red badge shows the **unread count**
- Click **"Mark all read"** to clear the badge

### Event Types

| Event | When It Fires |
|-------|---------------|
| Agent Launched | An agent is started from the dashboard |
| Agent Shutdown | An agent is stopped |
| Task Error | An agent's step result has status "failed" |
| Scheduled Task Fired | A cron/interval task sends a message to an agent |
| Webhook Triggered | An external service fires a webhook |
| Approval Needed | An agent requests user approval for a tool |
| Broadcast Sent | A broadcast message is sent to all agents |

Notifications are stored in memory (not persisted to disk) and capped at 50. They reset when the hub restarts.

---

## Broadcast

Send a single message to **all active agents** at once.

### How to Use

1. Go to the **Dashboard** tab
2. Below the Active Agents list, find the **"Broadcast to All Agents"** form
3. Type a message and click **"Broadcast"**
4. Every active agent receives the message in their queue

### Use Case

Useful for giving all agents the same instruction simultaneously, such as:
- "Stop all current tasks"
- "Switch to high-priority mode"
- "Report your current status"

---

## Scheduled Tasks

Create recurring tasks that run on a timer or cron schedule.

### Creating a Task

1. Go to the **Scheduled Tasks** tab
2. Select the **target agent** (must be running to receive tasks)
3. Choose a schedule type:
   - **Every N hours** — runs at a fixed interval
   - **Specific time (cron)** — runs at a specific hour/minute on specific days
4. Enter the **task/goal** text
5. Click **"Create Scheduled Task"**

### Schedule Types

| Type | Example |
|------|---------|
| Interval | Every 6 hours |
| Cron — daily | 9:00 AM every day |
| Cron — weekdays | 9:00 AM Mon-Fri |
| Cron — custom | 8:30 AM on Mon/Wed/Fri |

### Timezone

The scheduler timezone is set in **Admin Settings → Default Agent Settings → Scheduler Timezone**. This controls when cron tasks fire. Default is UTC.

### Managing Tasks

- **Enable/Disable**: Toggle a task on or off without deleting it
- **Delete**: Remove a task permanently
- **Next Run**: The table shows when each task will fire next

Tasks are persisted to `scheduled_tasks.json` and survive hub restarts.
