# Sentinel (Practical VM Edition)

Sentinel is now scoped to two practical governance use cases:

- Analyze every terminal command before execution.
- Analyze Gmail draft/send actions before execution.

Both flows are guarded by the Sentinel gateway and routed through LLM cognitive decisions.

## What was removed

To keep deployment VM-friendly and practical, the following are removed from the active setup:

- Kubernetes manifests
- Prometheus / Grafana stack
- Makefile-based workflows

## Stack

- `gateway` (FastAPI): policy and decision API
- `audit-worker`: writes events to Neo4j
- `redis`: rate-limit + event buffering
- `neo4j`: audit trail graph
- `dashboard`: web monitoring UI

## Run on VM

1) Configure `.env` (at minimum):

```env
SENTINEL_ADMIN_KEY=snl_admin_dev_changeme_replace_me
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4o-mini
FORCE_COGNITIVE_PATH=true
JUDGE_FORCE_TIER3_OPENAI=true
```

2) Start services:

```bash
docker compose -f docker-compose.yml up -d --build
```

3) Open:

- Gateway docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Dashboard: [http://localhost:3000](http://localhost:3000)

## Terminal Guard (all commands analyzed)

Install hooks:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_terminal_hooks.ps1
```

Open a new terminal after install.

Test blocked secret read:

```powershell
cat .env
```

Expected: blocked by Sentinel.

## Gmail Guard

Bootstrap OAuth once (if not already done):

```bash
python scripts/gmail_oauth_bootstrap.py --credentials secrets/gmail/credentials.json --token secrets/gmail/token.json
```

Test guarded draft:

```bash
python scripts/gmail_guarded_send.py --mode draft --to user@gmail.com --subject "Test" --body "hello"
```

## API quick check for LLM path

When polling `/v1/decisions/{id}`, confirm:

- `path` is `cognitive_path`

That confirms decisioning is routed through LLM cognitive flow.
