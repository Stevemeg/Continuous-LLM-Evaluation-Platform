"""Gate reports: the same decision, machine-readable and human-readable.

`REQ-F-09-4` requires both, "each containing the exact evidence on which the
decision rests". They are produced from one set of rows by one module, because
two reports assembled independently are two things that can disagree, and the
one a person reads is the one that would be believed.

The effective outcome is derived here rather than stored. A decision row is
audit-class and immutable; an exception is a later, separate, audited act. So
`evaluatedOutcome` is what the evidence produced, always, and `outcome` differs
from it only by `exception_applied`, and only while an exception is in force.
"""
from __future__ import annotations

from clep.security.privacy import redact_credentials

BLOCKING = ("hard_fail", "approval_required")


def effective_outcome(evaluated: str, live_exception: dict | None) -> str:
    """An exception waives a block. It cannot manufacture one, and it cannot
    upgrade a pass into something else."""
    if live_exception and evaluated in BLOCKING:
        return "exception_applied"
    return evaluated


def machine_readable(decision: dict, comparisons: list[dict],
                     criterion_results: list[dict],
                     live_exception: dict | None) -> dict:
    body = {
        "id": decision["id"],
        "projectId": decision["projectId"],
        "candidateRunId": decision["candidateRunId"],
        "baselineId": decision["baselineId"],
        "gatePolicyVersionId": decision["gatePolicyVersionId"],
        "statisticalMethodVersion": decision["statisticalMethodVersion"],
        "gateEvidenceDigest": decision["gateEvidenceDigest"],
        "evaluatedOutcome": decision["evaluatedOutcome"],
        "outcome": effective_outcome(decision["evaluatedOutcome"], live_exception),
        "decidedAt": decision["decidedAt"],
        "comparisons": [_comparison(c) for c in comparisons],
        "criterionResults": [
            {"metric": r["metric"], "dimension": r["dimension"],
             "verdict": r["verdict"], "ruleFired": r["ruleFired"],
             "detail": r["detail"]} for r in criterion_results],
    }
    if live_exception:
        body["exception"] = {
            "id": live_exception["id"], "actorId": live_exception["actorId"],
            "justification": live_exception["justification"],
            "expiresAt": live_exception["expiresAt"],
            "createdAt": live_exception["createdAt"]}
    return {k: v for k, v in body.items() if v is not None}


def _comparison(c: dict) -> dict:
    body = {"metric": c["metric"], "resultKind": c["resultKind"],
            "classification": c["classification"], "sampleSize": c["sampleSize"],
            "statisticalMethodVersion": c["statisticalMethodVersion"],
            "baselineMean": _num(c["baselineMean"]),
            "candidateMean": _num(c["candidateMean"]),
            "meanDifference": _num(c["meanDifference"]),
            "effectSize": _num(c["effectSize"]),
            "minimumSampleSize": c["minimumSampleSize"],
            "abstentionReason": c["abstentionReason"],
            "notComparableReason": c["notComparableReason"]}
    if c["intervalLower"] is not None:
        body["interval"] = {"lower": _num(c["intervalLower"]),
                            "upper": _num(c["intervalUpper"]),
                            "confidenceLevel": _num(c["confidenceLevel"])}
    return {k: v for k, v in body.items() if v is not None}


def _num(value):
    """Decimals cross the wire as strings.

    `Decimal` is what the store holds and what the statistics compute in; JSON
    numbers are binary floating point, and a gate decision that changed on the way
    out would be irreproducible for a reason nobody would look for.
    """
    return None if value is None else str(value)


def human_readable(decision: dict, comparisons: list[dict],
                   criterion_results: list[dict],
                   live_exception: dict | None) -> str:
    """Prose carrying the same evidence, not a summary of it.

    A human-readable report that dropped the interval, the sample size or the
    rule that fired would be the version everyone reads and the one that cannot
    be checked.
    """
    outcome = effective_outcome(decision["evaluatedOutcome"], live_exception)
    lines = [f"# Gate decision {decision['id']}",
             "",
             f"**Outcome: {outcome}**",
             ""]
    if outcome != decision["evaluatedOutcome"]:
        lines.append(f"The evidence produced `{decision['evaluatedOutcome']}`. "
                     f"An exception in force makes the effective outcome "
                     f"`{outcome}`; the decision itself is unchanged.")
        lines.append("")
    lines += [
        f"| | |", "|---|---|",
        f"| Candidate run | `{decision['candidateRunId']}` |",
        f"| Baseline | `{decision['baselineId'] or 'none'}` |",
        f"| Gate policy version | `{decision['gatePolicyVersionId']}` |",
        f"| Statistical method | `{decision['statisticalMethodVersion']}` |",
        f"| Evidence digest | `{decision['gateEvidenceDigest']}` |",
        f"| Decided at | {decision['decidedAt']} |",
        ""]

    if criterion_results:
        lines += ["## Criteria", "",
                  "| Metric | Dimension | Verdict | Rule | Detail |",
                  "|---|---|---|---|---|"]
        for r in criterion_results:
            lines.append(f"| `{r['metric']}` | {r['dimension']} | "
                         f"**{r['verdict']}** | {r['ruleFired']} | {r['detail']} |")
        lines.append("")

    deterministic = [c for c in comparisons
                     if c["resultKind"] == "deterministic_evaluator"]
    judged = [c for c in comparisons if c["resultKind"] == "probabilistic_judge"]
    operational = [c for c in comparisons if c["resultKind"] == "operational"]

    # REQ-F-08-6 in the report as well as in the store. Deterministic evaluator
    # results and probabilistic judge results are never shown in one table,
    # because a reader who scans a column of numbers will compare them.
    for title, group in (("Deterministic evaluators", deterministic),
                         ("Probabilistic judges", judged),
                         ("Operational metrics", operational)):
        if not group:
            continue
        lines += [f"## {title}", "",
                  "| Metric | Classification | Baseline | Candidate | "
                  "Difference | Interval | n | Effect size |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in group:
            interval = ("—" if c["intervalLower"] is None
                        else f"[{c['intervalLower']}, {c['intervalUpper']}] "
                             f"@ {c['confidenceLevel']}")
            lines.append(
                f"| `{c['metric']}` | **{c['classification']}** | "
                f"{_show(c['baselineMean'])} | {_show(c['candidateMean'])} | "
                f"{_show(c['meanDifference'])} | {interval} | {c['sampleSize']} | "
                f"{_show(c['effectSize'])} |")
        lines.append("")
        for c in group:
            if c["abstentionReason"]:
                lines.append(f"- `{c['metric']}` did not classify: "
                             f"{c['abstentionReason']}")
            if c["notComparableReason"]:
                lines.append(f"- `{c['metric']}` was not comparable: "
                             f"{c['notComparableReason']}")
        lines.append("")

    if live_exception:
        lines += ["## Exception in force", "",
                  f"- Actor: `{live_exception['actorId']}`",
                  f"- Expires: {live_exception['expiresAt']}",
                  f"- Justification: {live_exception['justification']}", ""]
    # `REQ-N-SEC-5` at the last surface before the report leaves the platform.
    # A justification is free text a human wrote, and a policy exception written
    # in a hurry is exactly where an operator pastes a token to explain how they
    # reproduced something. Redacting at the boundary rather than at every field
    # is what makes it true for fields added later.
    return redact_credentials("\n".join(lines))


def _show(value):
    return "—" if value is None else str(value)
