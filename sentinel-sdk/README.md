# sentinel-sdk

Thin Python client for the [Sentinel AI Governance Gateway](../README.md).  
Wrap your AI agent's tool calls with this SDK so every action is evaluated against your governance policies before execution.

## Installation

```bash
pip install sentinel-sdk
```

## Quick start

```python
from sentinel_sdk import AgentClient

async with AgentClient(
    gateway_url="https://your-gateway.up.railway.app",
    api_key="snl_...",          # issued by the gateway on agent registration
    agent_id="agent_my_bot",
) as sentinel:

    decision = await sentinel.check(
        tool_name="execute_payment",
        arguments={"amount": 75_000, "currency": "USD", "recipient": "vendor_abc"},
        context={"task_description": "Pay Q1 invoice per contract clause 4.2"},
    )

    if decision.is_allowed:
        run_payment(...)
    elif decision.needs_human:
        notify_approver(decision.decision_id)
    else:
        raise RuntimeError(f"Blocked: {decision.reason}")
```

## Verdicts

| Verdict | Meaning |
|---------|---------|
| `ALLOWED` | Policy cleared the action — proceed |
| `BLOCKED` | Policy denied the action — do not proceed |
| `HUMAN_REVIEW` | Ambiguous — a human must approve before proceeding |

## Running the full stack locally

```bash
# Clone the repo
git clone https://github.com/your-org/sentinel-ai && cd sentinel-ai

# Start all services (Redis, Neo4j, Ollama, Gateway, Dashboard)
cp .env.example .env          # fill in SENTINEL_ADMIN_KEY and OPENAI_API_KEY
docker compose up -d

# Pull the local judge model (one-time, ~2 GB)
docker exec sentinel-ollama ollama pull llama3.2:3b

# Gateway is now live at http://localhost:8000
# Dashboard is now live at http://localhost:3000
```

## License

MIT
