# Workflow Engine Guide

Chain agents together into automated pipelines. The output of one agent flows as input to the next, with optional approval gates and failure recovery.

---

## What Is a Workflow?

A workflow is a sequence of steps. Each step assigns a task to a specific agent. When that agent finishes, its output is passed to the next step's agent automatically.

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Step 1    │      │   Step 2    │      │   Step 3    │
│  Researcher │─────>│ Summarizer  │─────>│  Formatter  │
│             │      │             │      │             │
│ "Research   │      │ "Summarize  │      │ "Format as  │
│  AI safety" │      │  findings"  │      │  a report"  │
└─────────────┘      └─────────────┘      └─────────────┘
     output ──────────> input                output ──────> done!
```

---

## Quick Start

### 1. Launch Your Agents

Before running a workflow, the agents it references must be running. Go to the **Dashboard** tab and launch the agents you need.

### 2. Create a Workflow

1. Go to the **Workflows** tab
2. Enter a **Workflow Name** (e.g., "Research & Summarize")
3. Optionally add a **Description**
4. Click **"+ Add Step"** to add steps:

```
┌──────────────────────────────────────────────────┐
│  Workflow Builder                                │
│                                                  │
│  Name: [Research & Summarize              ]      │
│  Desc: [Research a topic and summarize    ]      │
│                                                  │
│  ┌─ Step 1 ────────────────────────────────────┐ │
│  │ Name: [Research]   Agent: [Researcher  v]   │ │
│  │ Task: [Research: {{trigger_input}}     ]     │ │
│  │ Next: [step2]  On Fail: []  Approval: [ ]   │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ┌─ Step 2 ────────────────────────────────────┐ │
│  │ Name: [Summarize]  Agent: [Summarizer  v]   │ │
│  │ Task: [Summarize: {{step1.output}}    ]      │ │
│  │ Next: []       On Fail: []  Approval: [x]   │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  [+ Add Step]                                    │
│  [Save Workflow]  [Clear]                        │
└──────────────────────────────────────────────────┘
```

5. Click **"Save Workflow"**

### 3. Run the Workflow

1. In the **Saved Workflows** table, click **"Run"** next to your workflow
2. Enter a **trigger input** (e.g., "AI safety and alignment research")
3. The workflow starts executing — Step 1's agent receives its task immediately

### 4. Watch It Run

The **Workflow Runs** section shows live progress:

```
┌──────────────────────────────────────────────────┐
│  Research & Summarize          [RUNNING]         │
│  Started 2 minutes ago                           │
│  Input: AI safety and alignment research         │
│                                                  │
│  ● step1 — Researcher          completed  14:30  │
│  │ "AI safety research focuses on ensuring       │
│  │  that artificial intelligence systems..."     │
│  │                                               │
│  ● step2 — Summarizer          running    14:31  │
│                                                  │
│                               [Cancel]           │
└──────────────────────────────────────────────────┘
```

When all steps complete, the run status changes to **COMPLETED**.

---

## Template Variables

Template variables let you pass data between steps. Use them in the **Task Template** field of each step.

| Variable | What It Contains |
|----------|-----------------|
| `{{trigger_input}}` | The text you entered when you clicked "Run" |
| `{{step1.output}}` | The output from step1 |
| `{{step2.output}}` | The output from step2 |
| `{{stepN.output}}` | The output from any step by its ID |

### Example

**Step 1** (ID: `step1`, Agent: Researcher):
```
Research the following topic in detail: {{trigger_input}}
```

**Step 2** (ID: `step2`, Agent: Summarizer):
```
Take these research findings and create a concise summary:

{{step1.output}}
```

**Step 3** (ID: `step3`, Agent: Formatter):
```
Format this summary as a professional report with sections and bullet points:

{{step2.output}}
```

---

## Approval Gates

Add a checkpoint where the workflow pauses and waits for your review before continuing.

### How to Set Up

Check the **"Approval"** checkbox on any step in the workflow builder.

### How It Works

```
Step 1 completes
       │
       ▼
┌─────────────────────┐
│  WORKFLOW PAUSED     │
│                      │
│  Step 2 is waiting   │
│  for your approval   │
│                      │
│  [Approve & Continue]│
└─────────────────────┘
       │  (you click Approve)
       ▼
Step 2 starts executing
```

1. When the workflow reaches a step with approval enabled, it **pauses**
2. The status changes to **PAUSED** (yellow badge)
3. A notification appears in the bell icon
4. You review the previous step's output in the run timeline
5. Click **"Approve & Continue"** to proceed
6. The approved step starts executing

### Use Cases

- Review research findings before summarizing
- Check a draft before publishing
- Verify data before sending to another agent

---

## Failure Recovery

Tell the workflow what to do when a step fails.

### On Failure Field

Each step has an **"On Failure"** field. Enter a step ID to jump to when the step fails.

| On Failure Value | What Happens |
|-----------------|-------------|
| *(empty)* | Workflow stops and is marked **FAILED** |
| `step1` | Jumps back to step1 and retries from there |
| `step3` | Jumps to step3 (skip ahead to a recovery step) |

### Example: Retry Pattern

```
Step 1: Research  ──> Step 2: Summarize ──> (done)
                          │
                          │ (on failure)
                          ▼
                      Step 1: Research
                      (retry from beginning)
```

Set step2's **On Failure** to `step1`. If the summarizer fails, the workflow re-runs from the research step.

---

## Workflow Statuses

### Workflow Run Statuses

| Status | Color | Meaning |
|--------|-------|---------|
| **RUNNING** | Blue | Workflow is actively executing a step |
| **PAUSED** | Yellow | Waiting for your approval on a step |
| **COMPLETED** | Green | All steps finished successfully |
| **FAILED** | Red | A step failed with no recovery path |
| **CANCELLED** | Gray | You manually cancelled the run |

### Step Statuses

| Status | Dot Color | Meaning |
|--------|-----------|---------|
| **running** | Blue | Agent is currently working on this step |
| **completed** | Green | Agent finished and produced output |
| **failed** | Red | Agent encountered an error |
| **waiting_approval** | Yellow | Paused, waiting for your click |
| **pending** | Gray | Not started yet |

---

## Step-by-Step Examples

### Example 1: Simple Two-Agent Chain

**Goal**: Research a topic, then summarize it.

| Field | Step 1 | Step 2 |
|-------|--------|--------|
| **Step ID** | step1 | step2 |
| **Name** | Research | Summarize |
| **Agent** | Researcher | Summarizer |
| **Task Template** | `Research: {{trigger_input}}` | `Summarize: {{step1.output}}` |
| **Next Step** | step2 | *(empty)* |
| **On Failure** | *(empty)* | *(empty)* |
| **Approval** | No | No |

Run it with input: `"quantum computing applications"`

### Example 2: Three-Agent Pipeline with Approval

**Goal**: Research, summarize with review, then format.

| Field | Step 1 | Step 2 | Step 3 |
|-------|--------|--------|--------|
| **Step ID** | step1 | step2 | step3 |
| **Name** | Research | Summarize | Format |
| **Agent** | Researcher | Summarizer | Formatter |
| **Task Template** | `Research: {{trigger_input}}` | `Summarize: {{step1.output}}` | `Format as report: {{step2.output}}` |
| **Next Step** | step2 | step3 | *(empty)* |
| **On Failure** | *(empty)* | step1 | step2 |
| **Approval** | No | Yes | No |

```
Research ──> [PAUSE for Review] ──> Summarize ──> Format ──> Done
                                       │
                                       │ (if fails)
                                       ▼
                                    Research (retry)
```

### Example 3: Same Agent, Multiple Steps

You can use the **same agent** in multiple steps:

| Step | Agent | Task |
|------|-------|------|
| step1 | Writer | `Write a first draft about: {{trigger_input}}` |
| step2 | Writer | `Improve this draft with better examples: {{step1.output}}` |
| step3 | Writer | `Final polish and proofread: {{step2.output}}` |

---

## Managing Workflows

### Edit a Workflow

1. In the Saved Workflows table, click **"Edit"**
2. The builder form populates with the workflow's current settings
3. Make changes, then click **"Save Workflow"**

### Enable / Disable

Click **"Disable"** to prevent a workflow from being run (without deleting it). Click **"Enable"** to re-activate.

### Delete a Workflow

Click **"Delete"** in the Saved Workflows table. This removes the definition but keeps any past run history.

### Cancel a Running Workflow

In the Workflow Runs section, click **"Cancel"** on any running or paused workflow.

---

## Notifications

The workflow engine sends notifications (visible in the bell icon) for:

| Event | Notification |
|-------|-------------|
| Workflow starts | "Workflow 'X' started" |
| Step completes | "Workflow 'X': step 'Y' completed by agent 'Z'" |
| Workflow pauses | "Workflow 'X' paused — awaiting approval for step 'Y'" |
| Workflow completes | "Workflow 'X' completed successfully" |
| Workflow fails | "Workflow 'X' failed at step 'Y'" |

---

## Data Storage

| Data | File | Persists? |
|------|------|-----------|
| Workflow definitions | `workflows.json` | Yes (survives restart) |
| Workflow run history | `workflow_runs.json` | Yes (survives restart) |

Run history is cleared when you use **Reset Hub** in Admin Settings. Workflow definitions are kept.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Workflow fails immediately | Check that the step's agent is **running** before starting the workflow |
| Step never completes | The agent must send a result back to the hub. Check agent logs (Dashboard → Logs button) |
| Template variable shows raw `{{step1.output}}` | Make sure the step ID in the variable matches the actual step ID exactly |
| Workflow stuck on RUNNING | The agent may be taking a long time. Check its logs, or Cancel and retry |
| "Workflow definition not found" error | The workflow was deleted while a run was active |
