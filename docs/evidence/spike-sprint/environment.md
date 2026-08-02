# Spike environment

Provisioned for the Technology Spike Sprint and recorded so the runs can be
reproduced or challenged. Everything below is throwaway: no spike dependency is
declared in the repository, and no spike code is application code.

## Infrastructure

| Component | How it ran | Purpose |
|---|---|---|
| PostgreSQL 16 (alpine) | container, host port 5439 | cost ledger, sample results, checkpoint table |
| Redis 7 (alpine) | container, host port 6399 | broker for the task-queue candidate |
| Temporal dev server | container `temporalio/temporal server start-dev`, host port 7239 | durable workflow engine candidate |
| llama.cpp server | container `ghcr.io/ggml-org/llama.cpp:server`, host port 8100, serving Qwen2.5-0.5B-Instruct Q4_K_M | a real self-hosted inference endpoint with real tokenisation, for `REQ-F-02-4` |
| Stub provider | local Python process, port 8099 | deliberate provider faults, including malformed responses |
| Docker Engine | 29.5.3, linux containers | container infrastructure ADR-001 said was absent |

A self-hosted endpoint was included because `REQ-F-02-4` makes self-hosted models
first-class and the fault endpoint is this spike's own code — reconciling an
abstraction against usage figures the spike itself wrote would prove only that
the plumbing connects. The llama.cpp server reports genuine tokenisation (36
prompt tokens for a prompt the stub scored at 7), so the reconciliation is
against a number no part of this spike chose.

## Libraries under test

Installed into a throwaway virtual environment outside the repository.

| Library | Version | Role |
|---|---|---|
| `temporalio` | 1.31.0 | durable execution candidate C1 |
| `arq` | 0.28.0 | task queue candidate C2 |
| `redis` | 5.3.1 | broker client |
| `psycopg` | 3.3.4 | PostgreSQL client |
| `litellm` | 1.95.0 | provider aggregation library, approaches A and C |
| `openai` | 2.52.0 | transitive dependency of litellm |
| Python | 3.11.0 | |

## Provider credentials

Two hosted-provider credentials were present in the environment and both were
used. Neither had spendable quota:

| Provider | Reachable | Result |
|---|---|---|
| OpenAI | yes | HTTP 429, `insufficient_quota` |
| Perplexity | yes | HTTP 401, `insufficient_quota` |

**No successful paid completion was purchased, and no charge was incurred.** The
spike reports zero billable tokens. This blocked one measurement and is recorded
as an evidence gap rather than worked around — but it also produced a finding
that a funded account would not have surfaced, since the same semantic condition
arrived under two different HTTP status codes.
