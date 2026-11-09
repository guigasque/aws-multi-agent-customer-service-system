# Requirements — NovaMart Multi-Agent Customer Support System

## Introduction

A production-grade multi-agent customer support system for the fictional e-commerce company
**NovaMart**, built with the **Strands Agents SDK** and **Amazon Bedrock AgentCore**. Five agents
work in an Orchestrator → Workers hierarchy: an OrchestratorAgent routes each customer request to
specialist workers (Inventory, Policy, Refund, Communication), which accumulate their findings in a
shared DynamoDB `WorkflowState` record (optimistic locking). The system is deployed to AgentCore
Runtime with Bedrock Guardrails, AgentCore Memory, and CloudWatch/X-Ray observability.

The sole implementation surface is `src/agent_orchestrator.py` (TODOs for project Tasks 2, 3, 4, 6).
Supporting AWS resources are provisioned by code: a CloudFormation foundation stack
(`infrastructure/starter_stack.yaml`, run as-is), the guardrail/runtime/memory/observability pipeline
(inside `agent_orchestrator.py`), and three Bedrock Knowledge Bases (a new code provisioner, with the
AWS Console as a documented fallback).

### Environment facts (this machine / account)

- **Credentials:** Udacity sandbox (assumed role `voclabs`, account `053471543567`), **temporary** —
  key + secret + **session token** in `starter/.env`, expire mid-session. CLI commands load them with
  `set -a; source .env; set +a`; `config.py` loads them via `load_dotenv()` for the Python process.
- **Region / project:** `us-east-1`, stack & project name `udacity-agentcore`.
- **Python:** project venv at `starter/.venv` (Python 3.13 locally; the deployed runtime is Python 3.12
  / arm64 — the AgentCore CLI fetches matching wheels).
- **Tooling:** AgentCore CLI `@aws/agentcore` **0.30.0** (global), Node 20.18, uv 0.13.
- **Installed SDKs (venv):** boto3 1.43.126, strands-agents 1.64.0, bedrock-agentcore 1.25.4,
  python-dotenv 1.2.4.
- The **system** AWS CLI (v1-era) lacks `bedrock-agentcore-control` and `s3vectors` subcommands;
  those services are reached through **boto3** (venv) and the AgentCore CLI, not the system `aws` CLI.

### Rubric traceability (120 points)

| Rubric area | Requirement(s) | Project task | Test |
|---|---|---|---|
| Worker agents (tools/models/temps) | R2, R3, R5 | 2.A/B/D | `task2` |
| Multi-agent RAG (parallel) | R4 | 2.C | `task2` |
| Orchestrator + WorkflowState | R1, R6 | 2.E | `task2` |
| Guardrail | R7 | 3 | `task3` |
| Runtime deploy | R8 | 3 | `task3` |
| Memory | R9 | 4 | `task4` |
| Knowledge Bases | R10 | 5 | `task5` |
| Observability | R11 | 6 | `task6` |

---

## Requirements

### R1 — Orchestrator routing
**User story:** As a NovaMart customer, I want my request understood and routed to the right
specialist, so that I get a correct answer without talking to a human.

**Acceptance criteria:**
1. WHEN any request arrives THEN the Orchestrator SHALL call `initialize_session` first.
2. WHEN the request is an order status / return / refund THEN it SHALL route to the Inventory agent,
   THEN the Refund agent.
3. WHEN the request is a policy-meaning question (return windows, shipping rates, warranty terms)
   THEN it SHALL route to the Policy agent.
4. WHEN the request is an account question ("what is my tier?", "am I premium?") THEN it SHALL route
   to the Inventory agent and SHALL NOT route to the Policy agent.
5. WHEN the request is a math/calculation question THEN it SHALL skip Inventory, Policy and Refund and
   go straight to Communication.
6. WHEN any request finishes THEN the Orchestrator SHALL route to the Communication agent as its very
   last tool call, and SHALL NOT write the final customer-facing response itself.
7. The Orchestrator SHALL use `config.ORCHESTRATOR_MODEL_ID` (Haiku 4.5) at temperature 0.0.

### R2 — Inventory agent (data gatherer)
1. `build_inventory_agent()` SHALL return one Strands `Agent` with exactly three tools:
   `check_order_status`, `get_customer_tier`, `list_customer_orders`.
2. `check_order_status` SHALL read `config.ORDERS_TABLE` with the **composite key**
   (`customer_id` partition + `order_id` sort).
3. `get_customer_tier` SHALL read `config.CUSTOMERS_TABLE` by `customer_id`.
4. It SHALL use `config.WORKER_MODEL_ID` (Sonnet 4.5) at temperature 0.1 and SHALL make no eligibility
   decisions (report facts only).

### R3 — Refund agent (decisions)
1. `build_refund_agent()` SHALL return one `Agent` with tools `get_inventory_context` and
   `initiate_refund`.
2. It SHALL read inventory findings from WorkflowState before deciding, and SHALL apply **30 days for
   Standard** and **60 days for Premium** return windows.
3. `initiate_refund` SHALL update the order record in DynamoDB and return a reference number.
4. It SHALL use `config.WORKER_MODEL_ID` at temperature 0.1.

### R4 — Policy agent (parallel multi-agent RAG)
1. `build_policy_agent()` SHALL create three retriever sub-agents — `ReturnsPolicyRetrieverAgent`,
   `ShippingPolicyRetrieverAgent`, `WarrantyPolicyRetrieverAgent` — each a full Strands `Agent` with a
   single tool calling `retrieve_from_knowledge_base()` with the matching
   `config.RETURNS_KB_ID` / `SHIPPING_KB_ID` / `WARRANTY_KB_ID`.
2. `search_all_policies` SHALL invoke all three retrievers in parallel with
   `ThreadPoolExecutor(max_workers=3)` + `as_completed()`, collect results, and return the combined
   text.
3. Retriever sub-agents SHALL use temperature 0.0; the coordinator SHALL use `config.WORKER_MODEL_ID`
   at temperature 0.2 and SHALL call `search_all_policies` before answering.
4. The coordinator SHALL expose exactly one tool (`search_all_policies`).

### R5 — Communication agent (final reply)
1. `build_communication_agent()` SHALL return one `Agent` with tool `get_full_workflow_context`.
2. It SHALL read the full WorkflowState and compose a warm, professional, complete reply.
3. It SHALL use `config.WORKER_MODEL_ID` at temperature 0.3.

### R6 — Shared WorkflowState
1. `initialize_session` SHALL create a WorkflowState record via `_create_workflow_state`.
2. Each `route_to_*` tool SHALL read current state, invoke its worker, and persist the result with
   `_update_workflow_state(session_id, {col: text}, expected_version)` using the current version.
3. Concurrent writes SHALL be safe (optimistic locking is provided; routing tools pass the right
   `expected_version`).

### R7 — Guardrail
1. `create_guardrail()` SHALL create a Bedrock Guardrail with:
   - content filters SEXUAL/VIOLENCE/HATE at **HIGH**, INSULTS/MISCONDUCT at **MEDIUM** (input+output);
   - PII **BLOCK** `CREDIT_DEBIT_CARD_NUMBER` + `US_SOCIAL_SECURITY_NUMBER`, **ANONYMIZE** `EMAIL` +
     `PHONE`;
   - one **DENY** topic per `config.GUARDRAIL_BLOCKED_TOPICS` (competitor products, pricing
     negotiations, legal threats), with `topicPolicyConfig.tierConfig={'tierName':'STANDARD'}` and
     top-level `crossRegionConfig={'guardrailProfileIdentifier':'us.guardrail.v1:0'}`;
   - the managed **PROFANITY** word list;
   - friendly blocked-input and blocked-output messages.
2. "Pricing negotiations" SHALL be defined narrowly (haggling / changing an advertised price) so that
   arithmetic with an already-specified price and discount is **allowed** (the `$29.99 × 5, 10% off`
   scenario must pass).
3. It SHALL publish a numbered version with `create_guardrail_version()` and return
   `(guardrail_id, guardrail_version)` (not DRAFT).

### R8 — Runtime deploy
1. `deploy_to_agentcore_runtime()` SHALL deploy via the AgentCore CLI helper:
   `configure_runtime(env_vars=..., network_mode='PUBLIC', protocol='HTTP',
   execution_role_arn=config.AGENTCORE_ROLE_ARN)`, `deploy()`, then return `deployed_runtime_arn()`.
2. Runtime environment variables SHALL include AWS_REGION, PROJECT_NAME, RETURNS_KB_ID,
   SHIPPING_KB_ID, WARRANTY_KB_ID, AGENT_LOG_GROUP, GUARDRAIL_ID, and a numbered GUARDRAIL_VERSION.
3. The runtime SHALL be READY with PUBLIC networking and HTTP protocol.

### R9 — Memory
1. `configure_memory()` SHALL call `agentcore_control.create_memory()` with a `summaryMemoryStrategy`
   (SESSION_SUMMARY), `eventExpiryDuration=7`, a descriptive name/description, and a `clientToken`.
2. It SHALL return the memory ARN (pre-written code waits for ACTIVE).

### R10 — Knowledge Bases
1. Three Bedrock Knowledge Bases SHALL exist — Returns (`policies/returns/`), Shipping
   (`policies/shipping/`), Warranty (`policies/warranty/`) — each using embedding model
   `amazon.titan-embed-text-v2:0` and the stack's S3 Vectors bucket + matching index.
2. Each KB's data source SHALL be synced (a completed ingestion job).
3. `.env` SHALL contain valid non-empty `RETURNS_KB_ID`, `SHIPPING_KB_ID`, `WARRANTY_KB_ID`.
4. `search_all_policies()` SHALL return non-empty passages from all three KBs.
5. KB creation SHALL be attempted **by code** first (`infrastructure/create_knowledge_bases.py`); the
   AWS Console runbook is the documented fallback.

### R11 — Observability
1. `configure_observability()` SHALL build a `loggingConfiguration` with `cloudWatchConfig`
   (`logGroupName=config.AGENT_LOG_GROUP`, `logLevel='INFO'`, `enabled=True`) and `xRayConfig`
   (`enabled=True`, `samplingRate=1.0`), and pass it to `apply_observability_config()` inside
   try/except.
2. A live request SHALL produce a connected X-Ray Service Map:
   `NovaMart-Orchestrator → worker agents → KnowledgeBase:*` nodes.

### R12 — Engineering standards (gate on every wave)
1. `python -m py_compile src/agent_orchestrator.py` SHALL be clean before hand-off.
2. New public functions SHALL have type hints and docstrings; tools/entrypoints SHALL handle specific
   exceptions and log at boundaries; model ids/temperatures SHALL come from `config` (no magic values).
3. Secrets SHALL never be committed or logged; `.env` stays gitignored.
4. "Ran without error" is not "works": each wave SHALL verify against the real success criterion and
   record the result honestly.

---

## Definition of done
- `python tests/test_agent.py all` → **120/120** (screenshot).
- `python src/agent_orchestrator.py test` → connected X-Ray Service Map (screenshot).
- `.env` populated with the three KB IDs, the runtime ARN, and the guardrail id + numbered version —
  never committed.
- A documented, runnable teardown path (`python infrastructure/cleanup.py`).
