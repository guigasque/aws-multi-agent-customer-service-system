# How to run

All commands run from `starter/`. AWS calls need the Udacity sandbox credentials in `.env`
(temporary — refresh when they expire).

## Prerequisites (one-time)

- Python venv with the project deps:
  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt bedrock-agentcore
  ```
- AgentCore CLI (Node 20+, uv):
  ```bash
  npm install -g @aws/agentcore@0.30.0 && agentcore --version   # 0.30.0
  ```
- Credentials in `.env` (not committed):
  ```
  AWS_ACCESS_KEY_ID=...
  AWS_SECRET_ACCESS_KEY=...
  AWS_SESSION_TOKEN=...
  AWS_REGION=us-east-1
  PROJECT_NAME=udacity-agentcore
  ```

## Load credentials

- Python reads `.env` automatically (`config.py` calls `load_dotenv()`).
- For shell AWS/CLI commands:
  ```bash
  set -a; source .env; set +a
  ```

## Deploy the foundation (once, from scratch)

```bash
set -a; source .env; set +a
aws cloudformation deploy \
  --template-file infrastructure/starter_stack.yaml \
  --stack-name udacity-agentcore \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
python infrastructure/seed_data.py
python config.py          # all foundation names resolve; KB/guardrail/runtime = "not yet set"
```

## Local test (Task 2, no cloud deploy needed beyond DynamoDB)

```bash
source .venv/bin/activate
python tests/test_agent.py task2
python src/agent_orchestrator.py test      # 3 scenarios, traced to X-Ray
python src/agent_orchestrator.py chat      # interactive
```

## Knowledge Bases (code-first)

```bash
python infrastructure/create_knowledge_bases.py       # describe-or-create 3 KBs, sync, write .env
# fallback: AWS Console runbook in this file (below) if the API/perms don't work
```

## Full deploy

```bash
python src/agent_orchestrator.py deploy    # guardrail → runtime → memory → observability
# copy printed AGENTCORE_RUNTIME_ARN / GUARDRAIL_ID / GUARDRAIL_VERSION into .env (done by code)
```

## Verify

```bash
python tests/test_agent.py task3
python tests/test_agent.py task4
python tests/test_agent.py task5
python tests/test_agent.py task6
python tests/test_agent.py all             # target: 120/120  (screenshot)
python src/agent_orchestrator.py test      # then screenshot the X-Ray Service Map
agentcore status                            # deployed runtime details
agentcore invoke '{"prompt":"What is the return policy for premium customers?","customer_id":"CUST-002"}'
```

X-Ray Service Map: AWS Console → CloudWatch → X-Ray traces → Service map → "Last 5 minutes" (allow
30–60 s after a run).

## Teardown (stop billing)

```bash
python infrastructure/cleanup.py           # dry run — lists what would be deleted
python infrastructure/cleanup.py --yes     # delete everything the project created
```

## Knowledge Base — AWS Console fallback

If `create_knowledge_bases.py` can't run in the sandbox: Bedrock → Knowledge Bases → Create
(self-managed). For each of Returns/Shipping/Warranty: data source S3 prefix
`policies/{returns,shipping,warranty}/`, embedding `amazon.titan-embed-text-v2:0`, vector store =
existing S3 Vectors bucket (`python config.py` → *Vector Bucket*) with index
`{returns,shipping,warranty}-policy-index`. Click **Sync**, then paste the three IDs into `.env`.

## When the sandbox token expires

AWS calls return `ExpiredToken` / `ExpiredTokenException`. Refresh the three `AWS_*` values in `.env`
and re-run the step — every provisioning step is idempotent (describe-or-create).
