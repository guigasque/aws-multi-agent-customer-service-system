NovaMart Multi-Agent Customer Support System — Submission
=========================================================

Contents
--------
  src/agent_orchestrator.py    Completed implementation (all TODOs done: Tasks 2, 3, 4, 6).
  .env                         Populated project values. The three AWS_* sandbox credentials
                               are REDACTED (temporary lab tokens, not part of the deliverable);
                               the required values are present:
                                 RETURNS_KB_ID, SHIPPING_KB_ID, WARRANTY_KB_ID
                                 AGENTCORE_RUNTIME_ARN
                                 GUARDRAIL_ID, GUARDRAIL_VERSION
  test_120_score_part1.png     `python tests/test_agent.py all` — Tasks 2/3/4/5 all PASS.
  test_120_score_part2.png     same run — Task 6 PASS and Score: 120/120 pts (100%).
  xray_service_map.png         CloudWatch X-Ray Service Map: NovaMart-Orchestrator connected to
                               the worker agents and the three KnowledgeBase nodes.

Verification
------------
  python tests/test_agent.py all   ->  120/120 pts (100%)
  Region us-east-1, account 053471543567, project udacity-agentcore.
