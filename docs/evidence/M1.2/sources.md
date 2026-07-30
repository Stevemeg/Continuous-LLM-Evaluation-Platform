# Source Register — M1.2

Milestone: **M1.2 — Competitive Analysis and Product Positioning**
Phase: 1 — Product Foundation

Every `[VERIFIED]` claim in [`../../product/competitive-analysis.md`](../../product/competitive-analysis.md) and [`../../product/positioning.md`](../../product/positioning.md) cites one or more source identifiers from this register. `check_m12.py` fails if a `[VERIFIED]` line carries no identifier, if a cited identifier is not defined here, or if an identifier defined here is never cited.

All sources are the vendor's own primary documentation or source repository. No secondary commentary, blog post, analyst summary, or aggregator listing is used as the basis of any `[VERIFIED]` claim.

## Access conditions

All sources were retrieved on **2026-07-30** from a general-purpose web fetch in the development environment, without authentication. Only publicly readable pages were used. No competitor product was installed, purchased, signed up for, or executed; nothing here rests on hands-on trial.

That bounds what this register can support. It records **what each vendor documents about itself on the pages retrieved**, which is not the same as what each product does in practice, and not the same as the vendor's complete documentation set.

## Register

| ID | Product | URL | Retrieved | What it is relied on to establish |
|---|---|---|---|---|
| `S-01` | Ragas | https://docs.ragas.io/en/stable/ | 2026-07-30 | Self-description as a library; LLM-driven and custom metrics; built-in dataset management; experiments-first framing; framework integrations |
| `S-02` | Promptfoo | https://www.promptfoo.dev/docs/intro/ | 2026-07-30 | Self-description as open-source CLI and library; local-first execution; CI/CD and GitHub Action integration; red teaming; provider breadth |
| `S-03` | Promptfoo Enterprise | https://www.promptfoo.dev/docs/enterprise/ | 2026-07-30 | Enterprise tier: RBAC, teams-based configurability, audit logging, sharing/export, on-prem with network isolation and dedicated runner |
| `S-04` | DeepEval | https://deepeval.com/docs/getting-started | 2026-07-30 | Self-description as open-source eval package; local execution; LLM-as-judge metrics; pytest integration; dataset and golden handling; synthetic generation; the Confident AI companion platform |
| `S-05` | OpenAI Evals | https://github.com/openai/evals | 2026-07-30 | Self-description as framework plus benchmark registry; MIT license; custom and private evals; stated contribution restriction |
| `S-06` | LangSmith | https://docs.langchain.com/langsmith/home | 2026-07-30 | Product framing around tracing and production metrics; dashboards and alerts; rules, webhooks and online evaluations; annotation queues; automated issue detection |
| `S-07` | LangSmith administration | https://docs.langchain.com/langsmith/administration-overview | 2026-07-30 | Organizations and workspaces; built-in and custom roles; RBAC as an Enterprise-gated feature; SSO; self-hosted deployment; usage, retention and rate limits |
| `S-08` | Arize Phoenix | https://github.com/Arize-ai/phoenix | 2026-07-30 | Self-description as open-source AI observability platform; Elastic License 2.0 and patent notice; OpenTelemetry-based tracing; evaluators; versioned datasets; experiments; prompt management; playground; self-hosting paths |
| `S-09` | Langfuse | https://github.com/langfuse/langfuse | 2026-07-30 | Self-description as open-source LLM engineering platform; MIT license excepting the enterprise-edition directories; tracing; prompt management with versioning; evaluation modes; datasets and experiments; self-hosting; multi-project support |
| `S-10` | Braintrust | https://www.braintrust.dev/docs/start | 2026-07-30 | Self-description as an observability platform for agents; evals and experiments; datasets and human feedback; playground; logging; CLI tooling; hosted commercial model |

## Retrieval failures, recorded rather than worked around

| Target | Outcome | Consequence |
|---|---|---|
| https://arize.com/docs/phoenix | HTTP 403 Forbidden | Phoenix claims rest on `S-08` (its source repository) only. Capabilities documented solely on the docs site are therefore outside this analysis, and Phoenix's access-control model is recorded as an evidence gap rather than as an absence. |

## Why absence is not recorded as absence

During retrieval, `S-06` did not mention RBAC, SSO, self-hosting, or audit controls. `S-07` documents all four, including built-in and custom roles.

The same product therefore appeared to lack four enterprise capabilities on one page and to document all of them on another. That is a property of which page was retrieved, not of the product.

This is the reason the analysis never converts "not found in the retrieved pages" into "the product does not have it." Where a capability matters to positioning and was not established, it is marked `[EVIDENCE GAP]` and the positioning claim that would have depended on it is withheld. `check_m12.py` enforces this mechanically: an unqualified absence assertion about a named competitor fails the check.
