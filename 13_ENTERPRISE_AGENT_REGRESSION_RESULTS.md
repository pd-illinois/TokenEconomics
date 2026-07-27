# Enterprise agent use-case regression results

**Execution date:** 2026-07-24  
**Lifecycle step:** `predict`  
**Evidence status:** Measured local development evidence; not production validation or release approval  
**Studio report:** `RPT-20260724-0CB42539` — 34-case seeded multi-model regression — 2026-07-24

## Objective

Exercise all 34 supplied enterprise agent descriptions through Studio's visible **Describe the solution you want to estimate** box while varying the provider/model. Verify execution, selected-model preservation, pricing/bound invariants, semantic topology classification, calculation output, and immutable receipt creation.

The output remains modeled workload-invocation economics. It is not a measurement of a deployed task trajectory, accepted-task quality, production capacity, Azure or local infrastructure cost, calibrated tail-risk probability, or savings.

## Reproducible random model assignment

The run used pseudorandom assignment with seed `20260724`, a linear congruential generator, and this priced-model pool:

- `anthropic:claude-sonnet-4`
- `azure_openai:gpt-4.1`
- `azure_openai:gpt-4.1-mini`
- `cohere:command-r-plus`
- `google:gemini-2.5-flash`
- `local:llama-3.1-70b`
- `mistral:mistral-large`
- `openai:gpt-4.1`
- `openai:gpt-4.1-mini`

Distribution in this seeded run:

| Provider/model | Cases |
|---|---:|
| `anthropic:claude-sonnet-4` | 6 |
| `azure_openai:gpt-4.1` | 5 |
| `azure_openai:gpt-4.1-mini` | 2 |
| `cohere:command-r-plus` | 3 |
| `google:gemini-2.5-flash` | 3 |
| `local:llama-3.1-70b` | 4 |
| `mistral:mistral-large` | 5 |
| `openai:gpt-4.1` | 4 |
| `openai:gpt-4.1-mini` | 2 |

## Fixed controls and expectation rubric

| Control | Value |
|---|---|
| Users | 1,000 |
| Calls per user per day | 10 |
| Daily modeled invocations | 10,000 |
| Submission path | Visible textarea → Studio click handler → `POST /api/plan` → local stdio MCP → immutable receipt |
| Single autonomous iterative actor | Expected `react_agent` |
| Ordered bounded processing stages | Expected `workflow` |
| Multiple named collaborating roles/agents | Expected `multi_agent` |

The expected topology is a reviewable regression contract, not an assertion that the wording uniquely determines one real implementation.

## Executive result

| Gate | Result |
|---|---:|
| HTTP 201 + completed Plan + selected model preserved | **34 / 34 passed** |
| Pricing marked verified | **34 / 34 passed** |
| P5 ≤ mean ≤ P95 | **34 / 34 passed** |
| Immutable Plan and receipt persisted | **34 / 34 passed** |
| Expected semantic topology | **8 / 34 passed (23.5%)** |

### Classification by category

| Category | Passed | Total |
|---|---:|---:|
| Agent use cases | 1 | 10 |
| Multi-agent use cases | 0 | 10 |
| Autonomous operations | 7 | 14 |

### Classification by expected topology

| Expected topology | Passed | Total |
|---|---:|---:|
| `react_agent` | 8 | 17 |
| `workflow` | 0 | 4 |
| `multi_agent` | 0 | 13 |

### Observed topology distribution

| Observed topology | Cases |
|---|---:|
| `single_call` | 19 |
| `react_agent` | 10 |
| `workflow` | 4 |
| `rag_pipeline` | 1 |
| `multi_agent` | 0 |

## Detailed results — agent use cases

All executions and model-preservation checks passed. Classification result compares expected and observed topology.

| ID | Use case | Random model | Expected → observed | Tokens P5 / mean / P95 | Model $/call | Monthly model $ | Classification |
|---|---|---|---|---:|---:|---:|---|
| A01 | Automated Competitor Analysis | `azure_openai:gpt-4.1` | `react_agent` → `rag_pipeline` | 3,472 / 4,960 / 7,440 | 0.017120 | 4,917.30 | Fail |
| A02 | End-to-End Invoice Processing | `local:llama-3.1-70b` | `workflow` → `single_call` | 1,050 / 1,500 / 2,250 | 0 | 0 | Fail |
| A03 | Context-Aware Customer Support Resolution | `anthropic:claude-sonnet-4` | `react_agent` → `single_call` | 420 / 600 / 900 | 0.004200 | 1,128.78 | Fail |
| A04 | Autonomous Bug Resolution | `mistral:mistral-large` | `react_agent` → `react_agent` | 29,807 / 108,403 / 207,696 | 0.361344 | 95,232.18 | **Pass** |
| A05 | Automated Lead Enrichment | `openai:gpt-4.1` | `react_agent` → `single_call` | 2,800 / 4,000 / 6,000 | 0.020000 | 5,635.50 | Fail |
| A06 | Clinical Notes Generation | `cohere:command-r-plus` | `workflow` → `single_call` | 1,050 / 1,500 / 2,250 | 0.008250 | 2,269.97 | Fail |
| A07 | Contract Review and Risk Assessment | `azure_openai:gpt-4.1-mini` | `workflow` → `single_call` | 1,050 / 1,500 / 2,250 | 0.001320 | 363.20 | Fail |
| A08 | Threat Investigation and Triage | `openai:gpt-4.1` | `react_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.006600 | 1,815.98 | Fail |
| A09 | Executive Briefing Preparation | `openai:gpt-4.1` | `react_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.006600 | 1,815.98 | Fail |
| A10 | Expense and Time Tracking Automation | `local:llama-3.1-70b` | `workflow` → `single_call` | 1,050 / 1,500 / 2,250 | 0 | 0 | Fail |

## Detailed results — multi-agent use cases

| ID | Use case | Random model | Expected → observed | Tokens P5 / mean / P95 | Model $/call | Monthly model $ | Classification |
|---|---|---|---|---:|---:|---:|---|
| M01 | Product Launch Orchestration | `azure_openai:gpt-4.1` | `multi_agent` → `workflow` | 15,761 / 37,854 / 60,960 | 0.173047 | 47,971.76 | Fail |
| M02 | Software Development Factory | `anthropic:claude-sonnet-4` | `multi_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.011700 | 3,214.76 | Fail |
| M03 | Customer Escalation Resolution Network | `local:llama-3.1-70b` | `multi_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0 | 0 | Fail |
| M04 | Enterprise Research and Report Generation | `mistral:mistral-large` | `multi_agent` → `single_call` | 2,800 / 4,000 / 6,000 | 0.016000 | 4,435.50 | Fail |
| M05 | Loan Processing Workflow | `azure_openai:gpt-4.1` | `multi_agent` → `workflow` | 2,234 / 9,883 / 18,602 | 0.043487 | 11,965.42 | Fail |
| M06 | Supply Chain Disruption Management | `openai:gpt-4.1` | `multi_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.006600 | 1,815.98 | Fail |
| M07 | Cybersecurity Threat Hunting | `azure_openai:gpt-4.1-mini` | `multi_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.001320 | 363.20 | Fail |
| M08 | Project Management Office Automation | `openai:gpt-4.1-mini` | `multi_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.001320 | 363.20 | Fail |
| M09 | Healthcare Care Coordination | `mistral:mistral-large` | `multi_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.005400 | 1,455.98 | Fail |
| M10 | Agentic Document Processing Pipeline | `mistral:mistral-large` | `multi_agent` → `workflow` | 2,234 / 9,883 / 18,602 | 0.035580 | 9,593.39 | Fail |

## Detailed results — autonomous operations

| ID | Use case | Random model | Expected → observed | Tokens P5 / mean / P95 | Model $/call | Monthly model $ | Classification |
|---|---|---|---|---:|---:|---:|---|
| O01 | Self-Healing Cloud Infrastructure | `anthropic:claude-sonnet-4` | `react_agent` → `react_agent` | 5,514 / 20,616 / 39,835 | 0.141016 | 37,705.93 | **Pass** |
| O02 | Autonomous Security Operations Center | `google:gemini-2.5-flash` | `react_agent` → `react_agent` | 6,314 / 21,416 / 40,635 | 0.006541 | 1,775.00 | **Pass** |
| O03 | Autonomous Data Pipeline Management | `anthropic:claude-sonnet-4` | `react_agent` → `react_agent` | 5,514 / 20,616 / 39,835 | 0.141016 | 37,705.93 | **Pass** |
| O04 | Continuous Compliance Monitoring and Remediation | `google:gemini-2.5-flash` | `react_agent` → `single_call` | 1,470 / 2,100 / 3,150 | 0.000855 | 244.47 | Fail |
| O05 | Autonomous Network Operations | `openai:gpt-4.1-mini` | `react_agent` → `react_agent` | 5,514 / 20,616 / 39,835 | 0.016163 | 4,337.99 | **Pass** |
| O06 | Predictive Maintenance and Asset Optimization | `mistral:mistral-large` | `react_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.005400 | 1,455.98 | Fail |
| O07 | Supply Chain Control Tower | `azure_openai:gpt-4.1` | `react_agent` → `react_agent` | 5,514 / 20,616 / 39,835 | 0.080816 | 21,689.93 | **Pass** |
| O08 | FinOps Cost Optimization Platform | `anthropic:claude-sonnet-4` | `react_agent` → `single_call` | 1,050 / 1,500 / 2,250 | 0.011700 | 3,214.76 | Fail |
| O09 | Autonomous Manufacturing Operations | `cohere:command-r-plus` | `react_agent` → `react_agent` | 29,807 / 108,403 / 207,696 | 0.542016 | 146,141.02 | **Pass** |
| O10 | Real-Time Fraud Prevention System | `google:gemini-2.5-flash` | `react_agent` → `workflow` | 3,034 / 10,683 / 19,402 | 0.003742 | 1,043.21 | Fail |
| O11 | Enterprise Operations Command Center | `local:llama-3.1-70b` | `multi_agent` → `react_agent` | 29,807 / 108,403 / 207,696 | 0 | 0 | Fail |
| O12 | Autonomous Revenue Operations | `cohere:command-r-plus` | `react_agent` → `react_agent` | 5,514 / 20,616 / 39,835 | 0.101021 | 27,112.42 | **Pass** |
| O13 | Agent-to-Agent Enterprise Knowledge Network | `azure_openai:gpt-4.1` | `multi_agent` → `single_call` | 2,800 / 4,000 / 6,000 | 0.020000 | 5,635.50 | Fail |
| O14 | Autonomous Transformation Office | `anthropic:claude-sonnet-4` | `multi_agent` → `react_agent` | 5,514 / 20,616 / 39,835 | 0.141016 | 37,705.93 | Fail |

## Immutable evidence

Studio persisted exactly 34 Plans and 34 receipts in report `RPT-20260724-0CB42539`. Prediction IDs are 1105–1138. Receipt identities by case are:

| Cases | Receipt IDs |
|---|---|
| A01–A10 | `plan_a38c54f7bfbc4105f892`, `plan_15d89a4a7cbe077e1902`, `plan_f9331c66560221148ff8`, `plan_ad3a75c317ce3065841e`, `plan_3e5cb38d84eacfc52fd0`, `plan_7aa1fe0f5a27cadb476d`, `plan_d17e6b0677d5e058ec18`, `plan_94fb7d931ccdb82ef3be`, `plan_c460c1476e1552bb8996`, `plan_95bed2591d0a7604d1b9` |
| M01–M10 | `plan_04842a7b89674f32c6e6`, `plan_7a82ecc70cc079c37e95`, `plan_c01bcdfd6bc775c68ca6`, `plan_ca2858bd89a5312cb3fb`, `plan_505cf7acce9e32aa212f`, `plan_5bb4eaae1d680571cb6b`, `plan_7af2ee1df8646d9afb7d`, `plan_1af4ab58561ccdd6e9b2`, `plan_64f18009ac89ed0eafea`, `plan_4fbdd74afaae8aab64ac` |
| O01–O14 | `plan_440454ab8369861e0881`, `plan_addcbd30a410b7670814`, `plan_4c440783115cd305e2fe`, `plan_b99a598de6039ecfd6a4`, `plan_6ea6d4a9be2d9882c83a`, `plan_26ef15f3caeef26936a4`, `plan_2282330b24ca070c44e2`, `plan_7fb847ecb565fe6e2245`, `plan_9d60a9ed6311dc704560`, `plan_c35911cbb11c27230e5a`, `plan_7e09799210aec21e70b0`, `plan_00bd22c2546a2c07ee92`, `plan_5b17eef610982627afc2`, `plan_a9cc9ba071793e7c2969` |

Each receipt contains its full SHA-256 snapshot hash. The report API returned exactly 34 Plan references and 34 receipt references after the run.

## Failure points

### F1 — Multi-agent role-list detection: critical

All 13 expected `multi_agent` cases failed classification. Studio currently recognizes explicit wording such as “multi-agent” or “4 agents,” but not multiple named role groups such as “research agents,” “writer agents,” and “reviewer agents.” This can reduce a multi-agent workload to `single_call`, `workflow`, or `react_agent`, materially understating repeated turns and context-sharing overhead.

### F2 — Ordered business workflows collapse to single call: high

All four expected `workflow` cases failed. Words such as ingest, extract, validate, create, route, review, and approve are not sufficient for the current adapter rules unless explicit workflow/multi-step phrasing is present. End-to-end invoice processing, clinical notes, contract review, and expense/time automation therefore received `SingleCall_TextOnly` estimates.

### F3 — Tool detection is RAG-only at the Studio adapter boundary: high

Descriptions explicitly mentioning web search, data retrieval, ERP, inventory, CRM, telemetry, CI/CD, code generation, tests, pull requests, refunds, and remediation mostly reported $0 tool cost. The Studio adapter currently supplies `file_search` only when it detects RAG/retrieval and otherwise sends an empty tool list, overriding FutureTokenPredictor's richer description classifier.

A01 is the only case with a nonzero tool charge because “data retrieval” triggered `rag_pipeline` and `file_search`. A04 still modeled iterative agent tokens but reported no tool charge despite code/test/PR actions.

### F4 — Non-text modality loss: high

Invoice ingestion and clinical-note generation were modeled as `SingleCall_TextOnly`. The adapter did not preserve implied document, image, or audio modalities from phrases such as invoices “in any format” and “listens to physician-patient conversations.” The resulting token and cost estimates are not adequate representations of those use cases.

### F5 — Keyword-driven false topology: medium/high

- A01 became `rag_pipeline` because “data retrieval” matched retrieval syntax, even though the statement describes a broader iterative agent.
- M01, M05, and M10 became `workflow` because orchestration/workflow/pipeline wording won over the multiple named agents.
- O10 became `workflow` because “workflows” appeared inside an autonomous control loop.
- O11 and O14 became `react_agent` because “autonomous” won over network/coordinating-agent semantics.

### F6 — Model-independent baseline plateaus: medium

Nineteen cases collapsed to `single_call`; many returned the same 1,500-token medium baseline despite materially different descriptions. Model selection changed price, but not the missing topology, modality, or tool behavior. This demonstrates that varied-model execution alone does not make the workload estimate semantically adequate.

### F7 — Local model $0 is not total cost: labeling risk

Four randomly assigned local-model cases showed $0 model API cost. This is not zero infrastructure cost: compute, accelerator, hosting, operations, and energy were not estimated. Studio's existing infrastructure exclusion remains essential and the result must not be described as a free workload.

### F8 — No transport, persistence, pricing, or bound-order failures observed

No case failed HTTP execution, Plan completion, provider/model preservation, pricing verification, bound ordering, or receipt persistence. Failures in this run are semantic modeling failures, not transport failures.

## Exact input corpus

The exact supplied descriptions were entered with their displayed use-case title prepended. No RAG wording was added.

### Agent cases

- **A01:** Automated Competitor Analysis: An LLM-powered agent takes a prompt to analyze a competitor, uses web search and data retrieval tools to collect information on products, pricing, partnerships, and announcements, then synthesizes a comprehensive competitive intelligence report with recommendations.
- **A02:** End-to-End Invoice Processing: An agent ingests invoices and purchase orders in any format, extracts key information using multimodal models, validates details against ERP and inventory systems, creates transactions automatically, and routes exceptions for human review.
- **A03:** Context-Aware Customer Support Resolution: A support agent reviews customer history, order data, product documentation, and previous tickets, executes approved actions such as refunds or shipment updates, and resolves cases with minimal human involvement.
- **A04:** Autonomous Bug Resolution: A software engineering agent monitors CI/CD pipelines and production telemetry, identifies the likely root cause of failures, generates code fixes, runs tests, validates results, and submits a pull request for review.
- **A05:** Automated Lead Enrichment: A sales agent researches inbound prospects across public and enterprise data sources, identifies decision makers, evaluates account fit, enriches CRM records, and drafts personalized outreach recommendations.
- **A06:** Clinical Notes Generation: A healthcare agent listens to physician-patient conversations, generates structured clinical documentation, captures medical observations, and drafts records for provider validation.
- **A07:** Contract Review and Risk Assessment: A legal agent analyzes contracts against organizational policies, highlights risky clauses, identifies deviations from standard terms, and generates recommended redlines.
- **A08:** Threat Investigation and Triage: A cybersecurity agent reviews security alerts, correlates telemetry across multiple tools, determines attack severity, and recommends or executes predefined response actions.
- **A09:** Executive Briefing Preparation: An executive assistant agent gathers meeting notes, emails, CRM records, operational reports, and news updates to create concise executive briefings with actions and risks.
- **A10:** Expense and Time Tracking Automation: An agent reviews calendars, meetings, emails, and development activity to draft timesheets, categorize expenses, and prepare submissions for employee approval.

### Multi-agent cases

- **M01:** Product Launch Orchestration: Research agents analyze market trends, content agents create campaign materials, legal agents review messaging, sales agents prepare account strategies, and coordination agents manage launch readiness across functions.
- **M02:** Software Development Factory: Planning agents generate requirements, coding agents write code, testing agents create and execute tests, security agents review vulnerabilities, and release agents prepare deployment packages.
- **M03:** Customer Escalation Resolution Network: Support agents gather case details, product agents analyze technical issues, billing agents review transaction history, and case management agents coordinate resolution and customer communications.
- **M04:** Enterprise Research and Report Generation: Research agents gather information from internal and external sources, analyst agents synthesize findings, writer agents draft reports, and reviewer agents validate quality and compliance.
- **M05:** Loan Processing Workflow: Document processing agents extract information from applications, risk agents perform credit evaluations, compliance agents check regulations, and approval agents prepare lending recommendations.
- **M06:** Supply Chain Disruption Management: Monitoring agents detect disruptions, sourcing agents identify alternative suppliers, logistics agents optimize transportation routes, and planning agents update fulfillment schedules.
- **M07:** Cybersecurity Threat Hunting: Detection agents monitor activity, investigation agents analyze attack paths, intelligence agents compare indicators against threat feeds, and remediation agents isolate affected systems.
- **M08:** Project Management Office Automation: Planning agents maintain schedules, risk agents identify delivery concerns, reporting agents generate executive dashboards, and resource management agents recommend staffing adjustments.
- **M09:** Healthcare Care Coordination: Intake agents collect patient information, scheduling agents coordinate appointments, care management agents track treatment plans, and communication agents engage patients and providers.
- **M10:** Agentic Document Processing Pipeline: Extraction agents read documents, classification agents categorize content, validation agents verify accuracy, and workflow agents route completed transactions to downstream systems.

### Autonomous operations cases

- **O01:** Self-Healing Cloud Infrastructure: Autonomous operations agents continuously monitor cloud environments, detect configuration drift or service degradation, execute corrective actions, validate system health, and document changes without human intervention.
- **O02:** Autonomous Security Operations Center (SOC): Security agents continuously monitor telemetry, investigate suspicious activities, isolate compromised assets, block malicious activity, and verify remediation actions in real time.
- **O03:** Autonomous Data Pipeline Management: Data agents monitor pipeline performance, detect failures and data quality issues, reroute workloads, optimize execution plans, and restore operations automatically.
- **O04:** Continuous Compliance Monitoring and Remediation: Compliance agents continuously evaluate infrastructure, applications, and business processes against policies, automatically remediate violations, and generate audit evidence.
- **O05:** Autonomous Network Operations: Network agents monitor performance and availability, identify outages or latency issues, reroute traffic, optimize configurations, and restore service levels automatically.
- **O06:** Predictive Maintenance and Asset Optimization: Operations agents continuously analyze telemetry from equipment, predict failures, schedule maintenance, order replacement parts, and optimize asset utilization.
- **O07:** Supply Chain Control Tower: Autonomous agents monitor inventory, supplier performance, transportation networks, and demand forecasts, then dynamically adjust procurement and logistics decisions.
- **O08:** FinOps Cost Optimization Platform: Financial operations agents continuously monitor cloud spending, identify waste, shut down unused resources, right-size infrastructure, and optimize resource allocation.
- **O09:** Autonomous Manufacturing Operations: Manufacturing agents monitor production lines, optimize scheduling, detect quality issues, adjust machine settings, and coordinate maintenance activities to maximize yield.
- **O10:** Real-Time Fraud Prevention System: Fraud detection agents analyze transaction streams, evaluate risk signals, suspend suspicious activities, initiate customer verification workflows, and continuously adapt detection strategies.
- **O11:** Enterprise Operations Command Center: A network of autonomous agents monitors business processes, customer experience, infrastructure, security, financial performance, and workforce operations, coordinating actions across systems to maintain predefined business outcomes.
- **O12:** Autonomous Revenue Operations: Agents continuously manage lead qualification, pipeline progression, forecasting, pricing compliance, renewal management, and revenue leakage prevention while coordinating with CRM and ERP systems.
- **O13:** Agent-to-Agent Enterprise Knowledge Network: Specialized agents across HR, Finance, IT, Sales, Operations, and Customer Support collaborate continuously to answer questions, execute actions, share context, and optimize enterprise-wide decision making.
- **O14:** Autonomous Transformation Office: Strategic planning, execution, risk, communications, financial tracking, and reporting agents coordinate large-scale transformation programs while continuously adjusting plans to achieve target outcomes.

## Release interpretation

This run passes multi-provider/model transport, bound ordering, and immutable evidence creation. It fails semantic forecast correctness for broad enterprise agent descriptions. These failures must remain release blockers until regression fixtures drive a richer, versioned intake contract or classifier that preserves topology, modalities, tools, and agent count without relying on users to insert implementation keywords.
