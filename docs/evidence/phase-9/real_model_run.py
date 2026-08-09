"""Drive the judge layer against real models, end to end, and record what happens.

The Phase 8 review carried one risk into Phase 9 as a priority: the judge layer
was correct against a specification and had never met a model. This script is
the answer to that, and it is deliberately the *real* path — the same
`OpenAICompatibleAdapter`, `ProviderGateway`, `run_judge`, `parse_reply`,
`regenerate_unreadable` and `reach_consensus` the application uses. Nothing here
is a stub wearing a different name.

## What "real" means here

Three self-hosted `llama.cpp` servers, each serving a different small instruct
model, on localhost. Real HTTP, real tokenisation, real sampling, real latency,
real refusals to follow the output format. No credentials are used, invented or
required, and no hosted provider is billed — the same arrangement the Technology
Spike Sprint used for `REQ-F-02-4`, and legitimate for exactly the same reason.

Three *different* models rather than one model repeated: ADR-017 §2 refuses an
ensemble that cannot disagree with itself, and running one model on three ports
would satisfy the letter of `configuration_key` while defeating the rule it
encodes. Three also means no configuration holds a majority, which is the rule
that keeps an ensemble from collapsing into one opinion with witnesses.

## What it does not prove

Small local models are not the models a customer would judge with. This
validates the **runtime path** — invocation, parsing, voting, consensus,
disagreement, escalation, regeneration — not the quality of any judgement. Model
quality is a calibration question and remains uncalibrated, as ADR-017 says.

Usage:
    python docs/evidence/phase-9/real_model_run.py [--out FILE]

Requires the servers described in `real-model-evidence.md`. If any is
unreachable the script exits non-zero and records that it did not run, rather
than falling back to anything.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")

from clep.agents.sdk import Bounds                       # noqa: E402
from clep.config import ProviderEndpoint                 # noqa: E402
from clep.evaluators.sdk import RetrievedContext, SampleContext  # noqa: E402
from clep.judges.consensus import Ensemble, reach_consensus      # noqa: E402
from clep.judges.reflection import is_unreadable, regenerate_unreadable  # noqa: E402
from clep.judges.sdk import JudgeVersion, render_prompt, run_judge  # noqa: E402
from clep.providers.gateway import Price, PriceBook, ProviderGateway  # noqa: E402
from clep.providers.openai_compatible import OpenAICompatibleAdapter  # noqa: E402
from clep.rag.hallucination import analyse                        # noqa: E402

JUDGES = (
    ("qwen", "http://localhost:8101/v1",
     "/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"),
    ("smollm", "http://localhost:8102/v1",
     "/models/smollm2-360m-instruct-q8_0.gguf"),
    ("llama", "http://localhost:8103/v1",
     "/models/llama-3.2-1b-instruct-q4_k_m.gguf"),
)

#: Two rubrics, both run, both recorded. The first is the one a reasonable
#: person writes first, and every judge abstained on every sample. The second
#: names the scale and forbids explanation. The difference between them is a
#: finding, not an embarrassment: rubric wording is a product artifact, and this
#: is what it costs to get it wrong.
RUBRICS = {
    "v1": ("You are grading an answer against a passage. Score how well the "
           "passage supports the answer."),
    "v2": ("Decide how strongly the PASSAGE supports the ANSWER to the "
           "QUESTION.\n"
           "1.0 = the passage states or directly entails the answer.\n"
           "0.5 = the passage is silent: it neither supports nor denies it.\n"
           "0.0 = the passage denies the answer or states the opposite.\n"
           "You must give a number. Only ABSTAIN if the answer field is empty. "
           "Never explain."),
}

#: Six samples, deliberately spread: two the passage clearly supports, two it is
#: silent on, two it denies. Small because each judgement is a real generation
#: and the point is the path, not a benchmark.
SAMPLES = [
    ("supported-1", "What is the capital of France?", "Paris",
     "France is a country in western Europe. Its capital is Paris."),
    ("supported-2", "How many legs does a spider have?", "Eight",
     "Spiders are arachnids. All arachnids have eight legs."),
    ("silent-1", "What is the population of Lyon?", "About 500,000",
     "Lyon is a city in France, at the confluence of the Rhone and the Saone."),
    ("silent-2", "Who wrote the score?", "Hans Zimmer",
     "The film was released in 1998 and ran for 122 minutes."),
    ("denied-1", "What is the capital of Australia?", "Sydney",
     "The capital of Australia is Canberra, not Sydney, which is the largest "
     "city."),
    ("denied-2", "Is the Earth flat?", "Yes",
     "The Earth is an oblate spheroid; the flat-Earth claim is false."),
]


def reachable(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(base_url.replace("/v1", "/health"),
                                    timeout=10) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def build(rubric: str):
    adapters, judges = {}, []
    for slug, base_url, model in JUDGES:
        endpoint = ProviderEndpoint(name=slug, base_url=base_url, api_key="",
                                    kind="self_hosted")
        adapters[slug] = OpenAICompatibleAdapter(endpoint, timeout=180.0)
        judges.append(JudgeVersion(slug=slug, version="1", model=model,
                                   endpoint_name=slug, rubric=rubric,
                                   max_tokens=24, temperature=0.0))
    # A declared price, so cost accounting runs on the real path too. Local
    # inference is not free — it is unpriced — and the figure below is a stated
    # local rate rather than a claim about anyone's bill.
    prices = PriceBook({model: Price(Decimal("0"), Decimal("0"))
                        for _, _, model in JUDGES})
    return ProviderGateway(adapters, prices), tuple(judges)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/evidence/phase-9/real-model-output.txt")
    parser.add_argument("--rubric", default="v2", choices=sorted(RUBRICS))
    args = parser.parse_args()

    unreachable = [slug for slug, url, _ in JUDGES if not reachable(url)]
    if unreachable:
        print(f"REFUSING: no real model reachable at {unreachable}. This script "
              f"does not fall back to a stub; a run that did not happen is "
              f"recorded as not having happened.")
        return 2

    gateway, judges = build(RUBRICS[args.rubric])
    ensemble = Ensemble(judges=judges, agreement_threshold=Decimal("0.25"),
                        minimum_scoring_votes=2)
    lines, rows = [], []
    started = time.time()

    lines.append(f"REAL-MODEL RUN (rubric {args.rubric}) - {len(JUDGES)} "
                 f"self-hosted llama.cpp servers, one model each")
    for slug, url, model in JUDGES:
        lines.append(f"  judge {slug:8} {url}  {model}")
    lines.append(f"  ensemble threshold 0.25, minimum scoring votes 2")
    lines.append("")

    for example_id, question, answer, passage in SAMPLES:
        sample = SampleContext(
            example_id=example_id, prompt=question, output=answer,
            integration_tier="partial",
            contexts=(RetrievedContext(f"{example_id}-c1", passage, 0),),
            citations=(f"{example_id}-c1",),
            required_context_ids=(f"{example_id}-c1",))
        prompt, neutralised = render_prompt(judges[0], sample)
        votes, notes = [], []
        for judge in judges:
            first = run_judge(judge, sample, gateway, timeout_ms=180_000)
            vote = first
            regenerated = 0
            if is_unreadable(first):
                reasoning = regenerate_unreadable(
                    judge, sample, gateway,
                    Bounds(max_iterations=3, budget=Decimal("1"),
                           timeout_ms=180_000),
                    timeout_ms=180_000)
                regenerated = reasoning.iterations
                if reasoning.value is not None:
                    vote = reasoning.value
            votes.append(vote)
            notes.append(f"{judge.slug}={vote.resolution}"
                         + (f":{vote.score}" if vote.is_scoring else "")
                         + (f" (regenerated x{regenerated})" if regenerated else "")
                         + (f" [{vote.detail[:60]}]" if vote.detail else ""))
        consensus = reach_consensus(ensemble, votes)
        rows.append({
            "example": example_id,
            "votes": [{"judge": v.judge.slug, "resolution": v.resolution,
                       "score": str(v.score) if v.score is not None else None,
                       "latency_ms": v.latency_ms,
                       "content_neutralised": v.content_neutralised,
                       "detail": v.detail[:200]} for v in votes],
            "state": consensus.state,
            "disagreement": str(consensus.disagreement),
            "disagreement_measured": consensus.disagreement_measured,
            "verdict": (str(consensus.verdict)
                        if consensus.verdict is not None else None),
            "escalation_reason": consensus.escalation_reason,
            "prompt_fenced": neutralised is False,
        })
        lines.append(f"[{example_id}] " + "; ".join(notes))
        lines.append(f"    consensus: {consensus.state}"
                     f" disagreement={consensus.disagreement}"
                     f" measured={consensus.disagreement_measured}"
                     f" verdict={consensus.verdict}"
                     f" reason={consensus.escalation_reason}")

    scoring = sum(1 for r in rows for v in r["votes"] if v["resolution"] == "scored")
    unreadable_count = sum(1 for r in rows for v in r["votes"]
                           if v["resolution"] == "failed")
    escalated = sum(1 for r in rows if r["state"] == "escalated")
    lines += [
        "",
        f"samples: {len(rows)}   judgements: {len(rows) * len(judges)}",
        f"scored: {scoring}   unreadable-or-failed: {unreadable_count}",
        f"escalated: {escalated}   agreed: {len(rows) - escalated}",
        f"elapsed: {time.time() - started:.1f}s",
        "",
        "Every number above came from a real generation. No stub was used and "
        "no credential was required.",
    ]

    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(args.out).with_suffix(".json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
