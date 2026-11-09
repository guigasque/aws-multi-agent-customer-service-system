# Implementation Plan — NovaMart Multi-Agent (wave-based)

Each wave is a self-contained unit on `src/agent_orchestrator.py` (+ new provisioning scripts/docs),
leaves the module importable and (where relevant) deployable, is independently testable, and ends as
one reviewable commit. Thin end-to-end first (agent graph + local Task 2), then cloud capabilities,
then KBs + full deploy + 120/120.

**Standards gate (every wave):** `python -m py_compile src/agent_orchestrator.py` clean; type hints +
docstrings on new functions; specific-exception handling + boundary logging; model ids/temps from
`config`; no secrets in code/logs; docs updated; relevant `tests/test_agent.py taskN` run and recorded
honestly.

**Checkbox legend:** `[x]` done & verified · `[~]` partial · `[ ]` not started.

**Progress:** Wave 1 ✅ (foundation) · Waves 2–4 ✅ (agent graph, task2 40/40) · Waves 5–7 ✅
(guardrail+runtime, memory, observability code) · Wave 8 ✅ (KBs by code + full deploy, **120/120**) ·
Wave 9 ✅ (docs + teardown runbook). **PROJECT COMPLETE.**

<!-- Wave 1 verified: stack CREATE_COMPLETE, 11 exports, seed OK, config.py resolves all names. -->


---

## Wave 1 — Spec, env, tooling, foundation from scratch
_Commit: `chore: spec, docs scaffold, venv/CLI, deploy foundation stack`_

- [x] 1.1 Spec committed under `.kiro/specs/novamart-multi-agent/` (requirements + design + this plan).
- [x] 1.2 Docs scaffold: `docs/HOW_IT_WORKS.md`, `docs/ARCHITECTURE.md`, `docs/BUILD_LOG.md`,
  `docs/HOW_TO_RUN.md`.
- [x] 1.3 Tooling verified by code: venv `.venv` with pinned SDKs, `agentcore --version` 0.30.0,
  boto3 reaches `bedrock-agentcore-control` + `s3vectors`. (R-env)
- [x] 1.4 Credentials: Udacity sandbox key/secret/token in `.env`; `aws sts get-caller-identity` OK.
- [x] 1.5 Deploy `udacity-agentcore` from `infrastructure/starter_stack.yaml`
  (`CAPABILITY_NAMED_IAM`, us-east-1); wait for `CREATE_COMPLETE`.
- [x] 1.6 `python infrastructure/seed_data.py`; `cp .env.example .env` (keep sandbox creds).
- [x] 1.7 Verify: `python config.py` resolves all foundation names; `CUST-001`/`ORD-27176` seeded.
  BUILD_LOG records stack id + resource names.

## Wave 2 — Worker agents: Inventory, Refund, Communication (project 2.A/B/D)
_Commit: `feat: inventory, refund, communication worker agents`_

- [x] 2.1 `build_inventory_agent` — 3 tools, composite-key `check_order_status`, Sonnet 0.1,
  data-gatherer prompt (no decisions). (R2)
- [x] 2.2 `build_refund_agent` — `get_inventory_context` + `initiate_refund`, 30/60-day windows,
  Sonnet 0.1. (R3)
- [x] 2.3 `build_communication_agent` — `get_full_workflow_context`, Sonnet 0.3, warm tone. (R5)
- [x] 2.4 Verify: `py_compile` clean; `check_order_status(CUST-001, ORD-27176)` returns the seeded
  order; tools/models/temps confirmed.

## Wave 3 — PolicyAgent: parallel multi-agent RAG (project 2.C)
_Commit: `feat: policy agent with parallel multi-agent RAG`_

- [x] 3.1 Three retriever sub-agents (Returns/Shipping/Warranty), each 1 tool → `retrieve_from_
  knowledge_base(config.*_KB_ID, query)`, temp 0.0. (R4.1)
- [x] 3.2 `search_all_policies` — `ThreadPoolExecutor(max_workers=3)` + `as_completed()`, `_run_
  retriever` helper, trace hooks, combined return. (R4.2)
- [x] 3.3 Coordinator PolicyAgent, temp 0.2, 1 tool, "call search_all_policies first" prompt. (R4.3/4)
- [x] 3.4 Verify: `py_compile` clean; with empty KB IDs `search_all_policies` ran 3 retrievers in
  parallel (~10s) and returned a graceful combined result; AST ThreadPoolExecutor check passes.

## Wave 4 — Orchestrator + full graph (project 2.E) → Task 2 green
_Commit: `feat: orchestrator routing + workflow state; task2 passing`_

- [x] 4.1 `initialize_session` (`_create_workflow_state`). (R6.1)
- [x] 4.2 Four `route_to_*` tools: read state → invoke worker → `_update_workflow_state(expected_
  version)`, with `trace.step_start/step_done`. (R6.2)
- [x] 4.3 Orchestrator system prompt enforcing all six routing rules; Haiku 0.0; Communication last;
  never writes the reply itself. (R1)
- [x] 4.4 Verify: `python tests/test_agent.py task2` → **40/40**; `python src/agent_orchestrator.py
  test` routed all three scenarios correctly (return→Inv→Refund→Comm; policy→Policy→Comm; math→Comm);
  X-Ray traces published.

## Wave 5 — Guardrail + runtime deploy (project Task 3)
_Commit: `feat: bedrock guardrail + agentcore runtime deploy`_

- [x] 5.1 `create_guardrail` — content/PII/topic/word policies per R7, STANDARD tier + crossRegion
  profile, pricing-negotiation defined to allow arithmetic; numbered version; return (id, version).
- [x] 5.2 `deploy_to_agentcore_runtime` — build `runtime_env`, `configure_runtime(PUBLIC/HTTP/role)`,
  `deploy()`, return `deployed_runtime_arn()`. (R8) [code done; live deploy in Wave 8]
- [x] 5.3 Verify: guardrail created standalone (id `y7qxjyotoito` v1, READY); `get_guardrail` read-back
  matches spec; ApplyGuardrail probe → math input/output NONE, negotiation/competitor/legal BLOCKED.

## Wave 6 — Memory (project Task 4)
_Commit: `feat: agentcore memory (session summary, 7-day)`_

- [x] 6.1 `configure_memory` — `create_memory` with `summaryMemoryStrategy`, `eventExpiryDuration=7`,
  name/description, clientToken; return arn. (R9) [code done; live create in Wave 8]

## Wave 7 — Observability (project Task 6)
_Commit: `feat: cloudwatch + x-ray observability config`_

- [x] 7.1 `configure_observability` — build `cloudWatchConfig`+`xRayConfig`, pass to
  `apply_observability_config` in try/except. (R11) [code done; live apply in Wave 8]

## Wave 8 — Knowledge Bases (code-first) + full deploy + 120/120 (project Task 5 + submission)
_Commit: `feat: knowledge base provisioner + full deploy; 120/120`_

- [x] 8.1 Verify S3 Vectors `create_knowledge_base` API shape + required IAM against boto3 1.43.126.
- [x] 8.2 `infrastructure/create_knowledge_bases.py` — describe-or-create 3 KBs over existing vector
  bucket/indexes, data sources on the 3 prefixes, Titan v2 embeddings, start+poll ingestion COMPLETE,
  write IDs to `.env`. CODE WORKED (no console fallback needed). (R10)
- [x] 8.3 `python src/agent_orchestrator.py deploy`; copied ARN + guardrail id/version into `.env`.
- [x] 8.4 Verify: `task3`/`task4`/`task5`/`task6` all pass; `python tests/test_agent.py all` →
  **120/120**. BUILD_LOG records all resource IDs.
- [x] 8.5 `python src/agent_orchestrator.py test` → 3 traces published; deployed `invoke` returns a
  grounded, guardrailed reply (trace published). Service Map ready to screenshot.

## Wave 9 — Docs finalize + teardown
_Commit: `docs: finalize doc set + teardown runbook`_

- [x] 9.1 Finalize HOW_IT_WORKS / ARCHITECTURE / BUILD_LOG (IDs, incidents+fixes) / HOW_TO_RUN
  (deploy/verify/teardown). Mark task statuses honestly.
- [x] 9.2 Record teardown: `python infrastructure/cleanup.py` (dry-run) then `--yes`. Confirmed
  dry-run runnable.

---

## Known risks / watch-items
- Sandbox token expiry mid-run → `ExpiredToken`; refresh `.env`, re-run (idempotent).
- Guardrail math false-positive if "pricing negotiations" defined too broadly (Wave 5).
- S3 Vectors KB-by-code may hit API/IAM gaps in the sandbox → Console fallback (Wave 8).
- `apply_observability_config` re-deploys the runtime; must run after Wave 5's deploy (handled by
  `deploy_all` ordering).
- First `agentcore deploy` also bootstraps CDK (CDKToolkit stack) — allow 5–10 min.
