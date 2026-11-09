# Architecture — NovaMart Multi-Agent Customer Support

Rendered diagrams (Mermaid) and prose for the Amazon Bedrock AgentCore + Strands multi-agent support
system. Diagrams are text (diff-friendly, no binaries) and render natively on GitHub. They are kept
consistent with the implementation; see
[`../.kiro/specs/novamart-multi-agent/design.md`](../.kiro/specs/novamart-multi-agent/design.md) for
the authoritative design and [`BUILD_LOG.md`](BUILD_LOG.md) for the build log.

---

## (a) System architecture

The deployed system runs in the AgentCore Runtime. One OrchestratorAgent (Haiku) routes each request
to four Sonnet worker agents; the PolicyAgent itself fans out to three retriever sub-agents, one per
Bedrock Knowledge Base. Workers never call each other — they share a single DynamoDB `WorkflowState`
record (optimistic locking). A Bedrock Guardrail is attached to every agent's model; AgentCore Memory
holds a per-session summary; X-Ray + CloudWatch trace and log every request.

```mermaid
flowchart LR
    client["Client<br/>test / chat / invoke"] --> rt["AgentCore Runtime<br/>BedrockAgentCoreApp (HTTP)"]
    rt --> orch["OrchestratorAgent<br/>Claude Haiku 4.5 · temp 0.0"]

    subgraph workers["Worker agents (Claude Sonnet 4.5)"]
        inv["InventoryAgent · 0.1<br/>check_order_status<br/>get_customer_tier<br/>list_customer_orders"]
        ref["RefundAgent · 0.1<br/>get_inventory_context<br/>initiate_refund"]
        pol["PolicyAgent · 0.2<br/>search_all_policies"]
        com["CommunicationAgent · 0.3<br/>get_full_workflow_context"]
    end
    orch -->|route_to_inventory_agent| inv
    orch -->|route_to_refund_agent| ref
    orch -->|route_to_policy_agent| pol
    orch -->|route_to_communication_agent| com

    subgraph retr["PolicyAgent sub-agents (temp 0.0, parallel)"]
        rr["ReturnsRetriever"]
        sr["ShippingRetriever"]
        wr["WarrantyRetriever"]
    end
    pol --> rr --> kbr[("Returns KB<br/>S3 Vectors")]
    pol --> sr --> kbs[("Shipping KB<br/>S3 Vectors")]
    pol --> wr --> kbw[("Warranty KB<br/>S3 Vectors")]

    inv <--> ddb[("DynamoDB<br/>orders · customers")]
    ref --> ddb
    orch <-->|create/read/update| ws[("DynamoDB WorkflowState<br/>version-based optimistic lock")]
    inv --> ws
    ref --> ws
    pol --> ws
    com -->|read| ws

    orch -. guardrail .-> gr["Bedrock Guardrail<br/>numbered version"]
    rt -. memory .-> mem["AgentCore Memory<br/>session summary · 7-day"]
    rt -. traces/logs .-> obs["X-Ray + CloudWatch"]
```

Everything except the foundation CloudFormation stack (DynamoDB, S3, S3 Vectors, IAM role, log group)
is provisioned as code by `src/agent_orchestrator.py` (guardrail, runtime, memory, observability) and
`infrastructure/create_knowledge_bases.py` (the three KBs). The Runtime is deployed by the AgentCore
CLI.

---

## (b) Single-request flow (return scenario)

An order/return request routes `initialize_session → Inventory → Refund → Communication`. Each worker
reads the current `WorkflowState`, does its job, and its output is written back under its own column
with an optimistic-lock version bump. Communication always runs last and writes the only
customer-facing text.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant O as Orchestrator (Haiku)
    participant WS as WorkflowState (DynamoDB)
    participant I as InventoryAgent
    participant R as RefundAgent
    participant M as CommunicationAgent

    C->>O: "return ORD-27176" (CUST-001, session_id)
    O->>WS: initialize_session → create record (version 0)
    O->>I: route_to_inventory_agent (read state v0)
    I->>WS: check_order_status + get_customer_tier → write inventory_agent (v1)
    O->>R: route_to_refund_agent (read state v1)
    R->>WS: get_inventory_context, apply tier window, initiate_refund → write refund_agent (v2)
    O->>M: route_to_communication_agent (read state v2)
    M->>WS: get_full_workflow_context (read all columns)
    M-->>C: final warm, grounded reply (orchestrator never writes it)
```

---

## (c) Orchestrator routing decision

The Haiku orchestrator (temperature 0.0, deterministic) routes on intent per the six system-prompt
rules. `initialize_session` always runs first; `route_to_communication_agent` always runs last.

```mermaid
flowchart TD
    start(["Customer prompt<br/>[session_id] [customer_id]"]) --> init["initialize_session<br/>(always first)"]
    init --> q{"Intent?"}
    q -->|"order status / return / refund"| inv["route_to_inventory_agent"] --> refd["route_to_refund_agent"]
    q -->|"policy meaning<br/>(windows, rates, warranty terms)"| pol["route_to_policy_agent"]
    q -->|"account question<br/>(my tier? am I premium?)"| invonly["route_to_inventory_agent<br/>(never policy)"]
    q -->|"math / calculation"| skip["skip Inventory/Policy/Refund"]
    refd --> comm
    pol --> comm
    invonly --> comm
    skip --> comm["route_to_communication_agent<br/>(always last — writes the reply)"]
    comm --> done(["Final customer-facing response"])
```

Rule 4 is the subtle one: account/tier questions go to **Inventory** (it holds customer data), never
**Policy** (which only knows policy text). Rule 5 keeps pure arithmetic away from the data workers but
still routes the answer through Communication.

---

## (d) Parallel multi-agent RAG — PolicyAgent

A policy question calls `search_all_policies`, which fans out to three retriever sub-agents
concurrently via `ThreadPoolExecutor(max_workers=3)` + `as_completed()`. Each retriever queries one
Bedrock Knowledge Base (Retrieve API over an S3 Vectors index); the coordinator synthesizes the three
results into one grounded answer. Running in parallel keeps total latency ≈ the slowest single
retrieval, not the sum.

```mermaid
flowchart LR
    q["Policy question"] --> sap["search_all_policies<br/>ThreadPoolExecutor(3)"]
    sap --> rr["ReturnsRetriever · 0.0"] --> ra["Retrieve API"] --> kbr[("returns-policy-index")]
    sap --> sr["ShippingRetriever · 0.0"] --> sa["Retrieve API"] --> kbs[("shipping-policy-index")]
    sap --> wr["WarrantyRetriever · 0.0"] --> wa["Retrieve API"] --> kbw[("warranty-policy-index")]
    rr -.->|"(domain, text)"| join["as_completed → combine"]
    sr -.-> join
    wr -.-> join
    join --> coord["PolicyAgent coordinator · 0.2<br/>synthesize grounded answer"]
```

### How the KBs are populated (ingestion)

Policy documents are seeded to one S3 bucket under three prefixes. Each Knowledge Base ingests its
prefix: chunk → embed with Amazon Titan Text Embeddings v2 → store vectors in its S3 Vectors index.
At query time the Retrieve API embeds the question and returns the top-k matching chunks.

```mermaid
flowchart LR
    seed["seed_data.py uploads<br/>policies/{returns,shipping,warranty}/"] --> s3[("S3 policy-docs bucket")]
    s3 -->|"data source + StartIngestionJob"| ing["chunk → embed (Titan v2)"]
    ing --> idx[("S3 Vectors index<br/>1024-dim · cosine")]
    subgraph query["At query time (RAG)"]
        qp["Customer question"] --> emb["embed question"] --> idx
        idx -->|"top-k chunks"| mdl["retriever answers from chunks"]
    end
```

The KB holds a snapshot taken at ingestion. `infrastructure/create_knowledge_bases.py` is idempotent
(describe-or-create by name); re-running it re-uses existing KBs and re-syncs only if needed.

---

## (e) Shared WorkflowState & optimistic locking

The single source of per-request truth is one DynamoDB item keyed by `session_id`. Each worker's
output lands in its own column; every write is a conditional update guarded by `version`, so
concurrent writers can't clobber each other — a losing writer re-reads and retries.

```mermaid
flowchart TD
    create["_create_workflow_state<br/>version 0, ttl 24h"] --> item[("WorkflowState item<br/>session_id (PK)")]
    item --> cols["columns filled as agents run:<br/>inventory_agent · policy_agent<br/>refund_agent · communication_agent"]
    upd["_update_workflow_state(updates, expected_version)"] -->|"ConditionExpression: version = expected_version"| chk{match?}
    chk -->|yes| bump["SET columns, version = expected+1"]
    chk -->|"no (ConditionalCheckFailed)"| retry["re-read current version → retry (≤3)"]
    retry --> upd
```

---

## (f) Deployment / provisioning order (all code)

Resources are created in dependency order, then verified, then torn down in reverse. The order in
`deploy_all()` is load-bearing: `configure_observability` re-runs `agentcore deploy` to push env
vars, so it must come **after** the runtime exists.

```mermaid
flowchart TD
    f1["aws cloudformation deploy<br/>starter_stack.yaml (CAPABILITY_NAMED_IAM)"] --> f2["seed_data.py<br/>customers, orders, policy docs"]
    f2 --> k["create_knowledge_bases.py<br/>3 KBs over existing S3 Vectors + sync → .env"]
    k --> d1["deploy 1/6: build 5-agent graph"]
    d1 --> d2["2/6: create_guardrail → numbered version"]
    d2 --> d3["3/6: deploy_to_agentcore_runtime<br/>configure_runtime(PUBLIC/HTTP/role) + agentcore deploy -y"]
    d3 --> d4["4/6: configure_memory → session summary, 7-day"]
    d4 --> d5["5/6: configure_observability<br/>CloudWatch + X-Ray (re-runs agentcore deploy)"]
    d5 --> d6["6/6: gateway (skipped — no Lambda tools)"]
    d6 --> v["tests/test_agent.py all → 120/120"]
    v --> td["Teardown (reverse): cleanup.py --yes"]
```

The first `agentcore deploy` also bootstraps CDK (`CDKToolkit` stack). Teardown is idempotent and
best-effort — run it twice to confirm the async KB/AgentCore deletes finished.

---

## (g) Configuration & credentials flow

`config.py` is the read path: resource names resolve lazily from CloudFormation **exports** (cached);
KB IDs, runtime ARN, and guardrail id/version come from `.env`. Provisioning scripts close the loop by
writing IDs back into `.env`. Temporary sandbox credentials also live in `.env` and are gitignored.

```mermaid
flowchart LR
    cfn["CloudFormation exports"] -->|"lazy, cached"| cfg["config.py<br/>table/bucket/role/log names"]
    env[".env (gitignored)<br/>KB IDs · runtime ARN · guardrail id+version · AWS creds"] -->|"load_dotenv()"| cfg
    kb["create_knowledge_bases.py"] -->|"writes RETURNS/SHIPPING/WARRANTY_KB_ID"| env
    dep["deploy pipeline"] -->|"writes AGENTCORE_RUNTIME_ARN, GUARDRAIL_ID/VERSION"| env
    cfg --> agents["agents + routing tools + deploy functions"]
    cli["AgentCore CLI"] -->|"agentcore.json env vars"| runtime["deployed runtime<br/>(same values at runtime)"]
```

No value is hand-edited twice: CloudFormation owns resource names, the provisioning scripts own the
IDs they create, and both feed a single `.env` + `config.py` read path used identically by local runs
and the deployed runtime.

---

## AWS resources (reference)

| Resource | Name / pattern | Created by |
|---|---|---|
| DynamoDB orders | `udacity-agentcore-orders` (PK `customer_id`, SK `order_id`) | stack |
| DynamoDB customers | `udacity-agentcore-customers` (PK `customer_id`) | stack |
| DynamoDB workflow state | `udacity-agentcore-workflow-state` (PK `session_id`, `version`) | stack |
| S3 policy docs | `udacity-agentcore-policy-docs-<acct>-<suffix>` | stack + seed |
| S3 Vectors bucket | `udacity-agentcore-vectors-…` | stack |
| Vector indexes ×3 | `returns-policy-index`, `shipping-policy-index`, `warranty-policy-index` | stack |
| IAM exec role | `udacity-agentcore-agentcore-role` | stack |
| CloudWatch log group | `/aws/bedrock/agentcore/udacity-agentcore` | stack / observability |
| Bedrock Guardrail | `udacity-agentcore-guardrail` (numbered version) | `create_guardrail` |
| AgentCore Runtime | `udacity_agentcore_runtime` | AgentCore CLI (`deploy`) |
| AgentCore Memory | `udacity_agentcore_memory*` (session summary, 7d) | `configure_memory` |
| Bedrock KBs ×3 | `novamart-{returns,shipping,warranty}-policy-kb` | `create_knowledge_bases.py` |

---

## (h) Observability path

Each request opens one X-Ray segment (`NovaMart-Orchestrator`). The traced `@tool` decorator
(`agent_observability.py`) opens a remote subsegment per `route_to_*` tool (→ worker node) and per KB
retrieval (→ `KnowledgeBase:*` node), so the Service Map shows the full call chain. Segments are
published with `xray:PutTraceSegments` identically from local runs (`test`/`chat`/`demo`) and inside
the deployed runtime (`invoke`). INFO logs ship to the project CloudWatch log group.

```mermaid
flowchart LR
    req["request"] --> seg["X-Ray segment<br/>NovaMart-Orchestrator"]
    seg --> n1["InventoryAgent"]
    seg --> n2["RefundAgent"]
    seg --> n3["PolicyAgent"]
    seg --> n4["CommunicationAgent"]
    n3 --> k1["KnowledgeBase:returns"]
    n3 --> k2["KnowledgeBase:shipping"]
    n3 --> k3["KnowledgeBase:warranty"]
    seg -->|"PutTraceSegments"| map["CloudWatch → X-Ray Service Map"]
    req -->|"INFO logs"| cw["/aws/bedrock/agentcore/udacity-agentcore"]
```
