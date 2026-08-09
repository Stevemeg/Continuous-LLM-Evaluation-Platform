# Real-model validation of the judge runtime path

The Phase 8 review carried one risk into Phase 9 as a priority: the judge layer
was correct against a specification and **had never met a model**. This is the
answer, and the distinction the review asked for — real evidence against
deterministic evidence — is drawn explicitly throughout.

## What was real

| Element | How |
|---|---|
| Provider | Three `llama.cpp` servers, one model each, on localhost |
| Models | Qwen2.5-0.5B-Instruct Q4_K_M · SmolLM2-360M-Instruct Q8_0 · Llama-3.2-1B-Instruct Q4_K_M |
| Transport | Real HTTP through `OpenAICompatibleAdapter`, the application's adapter |
| Everything after | `ProviderGateway` → `run_judge` → `render_prompt` → `parse_reply` → `Vote` → `reach_consensus`, unmodified |
| Credentials | **None.** No hosted provider was contacted, none was billed, and nothing was invented |

Three different models rather than one repeated. ADR-017 §2 refuses an ensemble
that cannot disagree with itself, and three ports serving one model would
satisfy the letter of `configuration_key` while defeating the rule it encodes.
Three also means no configuration holds a majority.

Reproduce with the servers from `real_model_run.py`'s header and:

```
python docs/evidence/phase-9/real_model_run.py --rubric v2
```

If a server is unreachable the script exits 2 and records that it did not run.
There is no fallback path, deliberately.

## The chain the review named, element by element

| Element | Validated | Evidence |
|---|---|---|
| Real model/provider | **yes** | three servers, real tokenisation and latency |
| Real judge invocation | **yes** | 18 generations in the three-model run |
| Real response | **yes** | including responses nobody designed |
| Real parser | **yes** | 4 replies rejected as unreadable, 12 read as scores |
| Real vote | **yes** | 12 scoring votes, 2 abstentions |
| Real consensus | **yes** | 6 consensus results |
| Real disagreement | **yes** | measured, values 0.0, 0.5 and 1.0 |
| Real escalation | **yes** | 5 × `disagreement_above_threshold`; and in the two-model run, 6 × `insufficient_scoring_votes` |
| Real abstention semantics | **yes** | an abstaining judge did not become a zero |
| Real regeneration | **yes** | fired 4 times, terminated on `no_progress` |
| Persisted evidence | **partial** — see the gap below |

## What real models did that scripted ones could not have

**A judge that abstains on everything.** Under rubric v1 — "score how well the
passage supports the answer", the rubric a reasonable person writes first —
Qwen2.5 abstained on all six samples with a coherent reason each time. Every
sample escalated on `insufficient_scoring_votes`. The rubric was the defect, and
no fixture would have suggested it, because a fixture returns what it was told
to return.

**A reply from the adversarial corpus, produced spontaneously.** Under rubric
v2, Qwen answered:

```
SCORE: 0.0\nABSTAIN: The passage does not...
```

That is `reply-multi-line` from `injection-corpus.json` — two answers in one
reply — arriving from a real model with no adversary involved. The parse
rejected it, as designed. The corpus case was not hypothetical.

**A bare number.** Qwen replied `0.0`, without the `SCORE:` prefix, four times.
Rejected, regenerated twice, and identical each time, so the loop terminated on
`no_progress` rather than paying for a third. The no-progress rule earned its
place against a real model on its first outing.

**A template placeholder as a reason.** Qwen once abstained with the literal
text `<reason>`.

## The finding that matters most

**The machinery was correct and the judges were useless.** Llama-3.2-1B scored
`denied-1` — the claim that Sydney is the capital of Australia, against a
passage stating it is Canberra — at **1.0**. The single `agreed` consensus in
the whole run was three judges agreeing that a passage refuting flat-Earth
supports the answer "Yes".

That is not a defect in this phase's code. It is the reason ADR-017 leaves the
agreement threshold unset and escalates by default, and the reason nothing in
this platform treats one judge as ground truth. A system that had quietly
averaged these votes would have produced confident, wrong numbers, and the
recorded behaviour instead was: five escalations to a human, and one agreement
that a human would have overturned.

It is also the sharpest possible statement of what remains uncalibrated. These
models are far below what anyone would judge with; the run validates the path,
**not** the quality of any judgement.

## Evidence gaps, stated

| Gap | Status |
|---|---|
| Hosted commercial providers | **Not exercised.** No credential with spendable quota is available, and none was invented. The Spike Sprint recorded the same gap for the same reason |
| Judgements persisted to the store through the real path | **Not exercised.** The run drives the library end to end and writes files; it does not go through `JudgeRepository`. The persistence path is covered by `tests/test_agentic_api.py` against a real database with deterministic votes |
| Judge quality | **Not measured, and not measurable here.** Requires calibration data, which requires models worth calibrating |
| Cost accounting on real calls | Exercised structurally at a declared local price of zero. A real bill has never been computed |

## Runs recorded

| File | What |
|---|---|
| `real-model-output-rubric-v1.txt` / `.json` | Two models, first rubric: every judge abstained, every sample escalated |
| `real-model-output-rubric-v2.txt` / `.json` | Two models, sharpened rubric: regeneration fires, one judge still never scores |
| `real-model-output.txt` / `.json` | Three models, sharpened rubric: the full chain, including measured disagreement and one agreement |
