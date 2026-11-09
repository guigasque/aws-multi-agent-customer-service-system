# Build log

Operational record: what was built, resource IDs, verification evidence, and incidents + fixes.
Newest entries at the bottom of each wave.

Account `053471543567` (Udacity `voclabs` sandbox), region `us-east-1`, project `udacity-agentcore`.

---

## Wave 1 — foundation from scratch

### Environment verified (by code)
- AgentCore CLI `@aws/agentcore` **0.30.0** (global), Node v20.18, uv 0.13.7.
- venv `starter/.venv`: boto3 1.43.126, strands-agents 1.64.0, bedrock-agentcore 1.25.4,
  python-dotenv 1.2.4.
- `aws sts get-caller-identity` → `arn:aws:sts::053471543567:assumed-role/voclabs/...`.
- boto3 reaches `bedrock-agentcore-control` (`list_agent_runtimes` → `[]`) and `s3vectors`
  (`list_vector_buckets` → `[]`): clean from-scratch state confirmed.
- NOTE: the **system** AWS CLI is too old for `bedrock-agentcore-control` / `s3vectors` subcommands —
  those go through boto3 (venv) and the AgentCore CLI. Expected; matches the reference project.

### Incident: wrong credentials initially
- First `sts get-caller-identity` showed IAM user `guigasque-dev` (account `911167901965`) with **no**
  CloudFormation permissions (`AccessDenied` on Describe/List/Exports). That account can't run the
  project (config.py reads CF exports for every resource name).
- Fix: switched to the Udacity sandbox creds (key/secret/session-token) in `.env`, loaded with
  `set -a; source .env; set +a`. Now `voclabs` role in account `053471543567`, CloudFormation OK.

### Foundation stack
- Deployed `udacity-agentcore` via `aws cloudformation deploy` (CAPABILITY_NAMED_IAM, us-east-1) →
  `CREATE_COMPLETE`.
- StackId: `arn:aws:cloudformation:us-east-1:053471543567:stack/udacity-agentcore/b71f0d60-dad9-11f1-8059-0afffc89f43f`
- Exports (11):
  - OrdersTable `udacity-agentcore-orders`, CustomersTable `udacity-agentcore-customers`,
    WorkflowStateTable `udacity-agentcore-workflow-state`
  - PolicyBucket `udacity-agentcore-policy-docs-053471543567-b71f0d60`
  - VectorBucket `udacity-agentcore-vectors-053471543567-b71f0d60`
    (ARN `arn:aws:s3vectors:us-east-1:053471543567:bucket/udacity-agentcore-vectors-053471543567-b71f0d60`)
  - Indexes `returns-policy-index`, `shipping-policy-index`, `warranty-policy-index`
  - AgentCoreRoleArn `arn:aws:iam::053471543567:role/udacity-agentcore-agentcore-role`
  - AgentLogGroup `/aws/bedrock/agentcore/udacity-agentcore`

### Seed + verify
- `python infrastructure/seed_data.py`: 4 customers, 11 orders (incl. CUST-001/ORD-27176 "Wireless
  Headphones Pro", delivered 2026-10-26), 6 policy docs across returns/shipping/warranty prefixes.
- `python config.py`: all foundation names resolve; KB/runtime/guardrail show "not yet set" (expected).
- `.env` created from `.env.example` structure + sandbox creds; confirmed gitignored
  (`git check-ignore starter/.env`).
- Wave 1 ✅ done & verified.

---

## Wave 2 — worker agents
- `build_inventory_agent` (3 tools, composite-key get_item, Sonnet 0.1), `build_refund_agent`
  (`get_inventory_context`+`initiate_refund`, 30/60-day windows, Sonnet 0.1), `build_communication_
  agent` (`get_full_workflow_context`, Sonnet 0.3). Order schema from seeder: customer_id, order_id,
  product_name, product_category, price, quantity, status, order_date, estimated_delivery,
  tracking_number. `initiate_refund` sets status `return_initiated` + return_reference/reason/ts.
- Verified: `check_order_status(CUST-001, ORD-27176)` → seeded "Wireless Headphones Pro", delivered.
  Wave 2 ✅.

## Wave 3 — policy agent (parallel RAG)
- 3 retriever sub-agents (temp 0.0), coordinator temp 0.2, 1 tool. `search_all_policies` fans out via
  `ThreadPoolExecutor(max_workers=3)` + `as_completed()`, honors `trace.kb_start/kb_done/kb_result`.
- Verified: parallel run ~10s (not 3×10s); AST ThreadPoolExecutor+submit check passes; graceful
  "no results" with empty KB IDs (live Bedrock reasoning confirmed). Wave 3 ✅.

## Wave 4 — orchestrator + task2 green
- `initialize_session` + 4 `route_to_*` tools (shared `_route` helper: read state → `step_start` →
  invoke worker → `_update_workflow_state(expected_version)` → `step_done`). Haiku 0.0, 6 routing
  rules in prompt, Communication always last, orchestrator never writes the reply.
- Verified: `tests/test_agent.py task2` → **40/40**. Live `test`: return→Inv(check_order+tier)→Refund
  →Comm; policy→Policy(3 parallel retrievers)→Comm; math→Comm-only. X-Ray traces published. Wave 4 ✅.

## Wave 5 — guardrail + runtime
- `create_guardrail`: content SEXUAL/VIOLENCE/HATE=HIGH, INSULTS/MISCONDUCT=MEDIUM; PII BLOCK
  card+SSN, ANONYMIZE email+phone; 3 DENY topics (competitor/pricing/legal) with STANDARD tier +
  crossRegion `us.guardrail.v1:0`; PROFANITY. "Pricing negotiations" defined narrowly to exclude
  arithmetic. → Guardrail `y7qxjyotoito` version **1**, READY.
- `deploy_to_agentcore_runtime`: builds runtime_env (region, project, 3 KB IDs, log group, guardrail
  id+version), `configure_runtime(PUBLIC/HTTP, role=udacity-agentcore-agentcore-role)`, `deploy()`,
  returns ARN.
- Verified (ApplyGuardrail probe): math input/output → `NONE` (allowed); negotiation / competitor /
  legal → `GUARDRAIL_INTERVENED` (blocked). The math false-positive is avoided. Wave 5 ✅.

## Wave 6 — memory
- `configure_memory`: `create_memory` with `summaryMemoryStrategy` (name SessionSummary, namespace
  `/summaries/{actorId}/{sessionId}`), `eventExpiryDuration=7`, clientToken.
  → Memory `udacity_agentcore_memory-8YT1e95xsJ`, ACTIVE. Wave 6 ✅.

## Wave 7 — observability
- `configure_observability`: `cloudWatchConfig` (log group, INFO, enabled) + `xRayConfig` (enabled,
  samplingRate 1.0) → `apply_observability_config` in try/except. Enabled CloudWatch Transaction
  Search + X-Ray 100% indexing; runtime env vars re-applied via `agentcore deploy`. Wave 7 ✅.

## Wave 8 — knowledge bases + full deploy + 120/120
### KBs by code (Task 5)
- `infrastructure/create_knowledge_bases.py` (NEW, outside the do-not-modify set). Reuses the stack's
  AgentCore role as the KB service role (trust already allows `bedrock.amazonaws.com`; has
  `bedrock:InvokeModel`, `s3:GetObject/ListBucket`, `s3vectors:*`). No new IAM role.
- **Incident:** first `create_knowledge_base` failed with `ValidationException: Vector index name
  should not be present with namespace arn`. S3 Vectors `s3VectorsConfiguration` accepts `indexArn`
  OR `indexName`, not both. **Fix:** pass only `indexArn` (it encodes the name). Re-ran: success.
- KBs created + synced (ingestion COMPLETE), IDs written to `.env`:
  - RETURNS_KB_ID `KXERZFZZVB` (data source `LAGAVCA80P`)
  - SHIPPING_KB_ID `L1EHXML1CO` (data source `YUGX7LR9VD`)
  - WARRANTY_KB_ID `JKBVLCTK3Z` (data source `PR2VCTSYMU`)
- Code-first worked; **no console fallback needed**.
- Verified: all three KBs return passages (top score ~0.69) for the premium-return/warranty query.

### Full deploy
- `python src/agent_orchestrator.py deploy`: guardrail (reused) → runtime → memory → observability →
  gateway (skipped, no Lambda functions). First deploy bootstrapped CDK (CDKToolkit) + synthesized
  `AgentCore-udacity-default`.
  - Runtime ARN: `arn:aws:bedrock-agentcore:us-east-1:053471543567:runtime/udacity_agentcore_runtime-gNqtAS68uD`
    (READY, PUBLIC, HTTP), runtime id `udacity_agentcore_runtime-gNqtAS68uD`.
- NodeVersionSupportWarning (node 20 vs future node 22) is a non-blocking CLI warning; deploy
  succeeded.

### Verification
- `python tests/test_agent.py all` → **120/120** (Task 2 40 + Task 3 20 + Task 4 15 + Task 5 20 +
  Task 6 20).
- `python src/agent_orchestrator.py test`: return→Inv→Refund→Comm; policy→Policy(3 KBs)→Comm;
  math→Comm. 3 X-Ray traces published (e.g. `1-6aef73a7-...`, `1-6aef73d8-...`, `1-6aef73fb-...`).
- Deployed `invoke` (CUST-002, premium policy) returned a reply through AgentCore Runtime with the
  guardrail attached and an X-Ray trace (`1-6aef7424-...`) — the deployed path, not just local.
- Wave 8 ✅.

## Wave 9 — docs + teardown
- Doc set written and later rewritten in plainer prose (HOW_IT_WORKS, ARCHITECTURE, HOW_TO_RUN, this
  BUILD_LOG) with the real IDs and the KB incident/fix. Task statuses in tasks.md marked honestly.
  `evidence/README.md` added as the illustrated report (screenshots embedded).
- Teardown dry-run (`python infrastructure/cleanup.py`) found all project resources, including the
  code-created `novamart-*` KBs, and kept the shared AgentCore role. Run with `--yes` to delete after
  the screenshots are captured.
- Wave 9 ✅.

---

## Submission checklist
- [x] `src/agent_orchestrator.py` — all TODOs implemented (Tasks 2/3/4/6).
- [x] `.env` populated: 3 KB IDs, runtime ARN, guardrail id + numbered version. Gitignored.
- [x] Screenshot: `python tests/test_agent.py all` → 120/120 (`evidence/t1.png` + `t-final.png`).
- [x] Screenshot: X-Ray Service Map (`evidence/x-ray.png`) — all 8 nodes, reviewer-approved.
- [ ] After final use: `python infrastructure/cleanup.py --yes` to stop billing.
