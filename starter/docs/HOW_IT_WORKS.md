# How it works — NovaMart multi-agent support

Start here: what the system does and how a request moves through it.

## The idea

NovaMart's support desk gets thousands of requests a day — "where's my order", "can I return this",
"what's the warranty", "how much is 5 of these with 10% off". This system answers them with five
agents instead of a person looking things up by hand.

An **Orchestrator** reads the request, decides who should handle it, and hands off to the
specialists. The specialists don't talk to each other — each does one job and writes what it found
into a shared DynamoDB record (the **WorkflowState**). The Orchestrator reads that record between
steps and, at the end, always asks the **Communication** agent to write the reply the customer sees.
The Orchestrator never writes that reply itself.

## The five agents

- **Orchestrator** (Claude Haiku 4.5, fast, temperature 0) — routing only. Decides inventory vs
  policy vs refund vs pure-math, in what order, and calls Communication last.
- **Inventory** (Sonnet, 0.1) — looks up orders and customer tier in DynamoDB. Reports facts; makes
  no decisions.
- **Refund** (Sonnet, 0.1) — decides return eligibility using the facts Inventory gathered and the
  tier window (Standard = 30 days, Premium = 60 days), then records the return.
- **Policy** (Sonnet, 0.2) — answers policy-meaning questions by querying three retriever sub-agents
  **at the same time** (Returns / Shipping / Warranty knowledge bases) and combining what they find.
- **Communication** (Sonnet, 0.3) — reads the whole WorkflowState and writes the final reply.

## A request, step by step

Take "I want to return my order ORD-27176" from CUST-001:

1. Orchestrator calls `initialize_session` → a blank WorkflowState row keyed by `session_id`.
2. It routes to **Inventory**, which reads the order (composite key: customer + order) and the
   customer's tier, and writes those facts into WorkflowState.
3. It routes to **Refund**, which reads Inventory's facts, applies the 30/60-day window, decides
   eligibility, records the return, and writes the decision.
4. It routes to **Communication**, which reads everything and writes the reply the customer gets.

A policy question ("return policy for premium customers?") skips Inventory/Refund: Orchestrator →
Policy (3 parallel retrievers) → Communication. A pure-math question ("5 × $29.99 with 10% off")
skips all three data workers: Orchestrator → Communication.

## Why WorkflowState (and optimistic locking)

Every write to the shared record is a conditional DynamoDB update guarded by a `version` number. If
two updates race, the second one's condition fails and it retries after re-reading. That keeps the
record consistent even though several agents touch it in one request, and it makes the whole request
auditable from a single item.

## Safety, memory, observability

- **Guardrail** (Bedrock) filters harmful content, blocks or anonymizes PII, and denies off-topic
  subjects (competitors, haggling, legal threats) while still letting ordinary arithmetic through.
- **Memory** (AgentCore) keeps a session summary so a multi-turn chat doesn't make the customer
  repeat themselves.
- **Observability** — every request is one X-Ray trace (Orchestrator → each worker → each KB), and
  INFO logs go to CloudWatch, the same locally and in the deployed runtime.

See `ARCHITECTURE.md` for the component view, `HOW_TO_RUN.md` to deploy and test, and `BUILD_LOG.md`
for what was built, the resource IDs, and what went wrong on the way.
