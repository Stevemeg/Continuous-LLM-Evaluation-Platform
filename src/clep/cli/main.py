"""`clep` — the command a CI job runs.

Three subcommands, and nothing that decides anything:

    clep gate      evaluate a candidate run against an approved baseline
    clep decision  fetch a decision that was already made
    clep analysis  fetch the retrieval and trajectory evidence for one sample

Everything goes through the same services the HTTP API uses. `REQ-F-10-3` is
structural here rather than promised: there is no subcommand that changes a
production system, a dataset, a policy or a release decision. The CLI reports,
and the exit code is the only thing it hands back to the pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from clep.api.gate_service import GateService
from clep.cli import exit_codes
from clep.identity import is_ulid

USAGE = """\
examples:
  clep gate --project P --run R --policy V --baseline B
  clep gate --project P --run R --policy V --baseline B --format json
  clep decision --id D --format markdown
  clep analysis --sample S
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clep", epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Release gates for AI quality, for CI systems.")
    parser.add_argument("--dsn", default=os.environ.get("CLEP_RUNTIME_DSN"),
                        help="runtime DSN; defaults to CLEP_RUNTIME_DSN")
    parser.add_argument("--organization", default=os.environ.get("CLEP_ORGANIZATION"),
                        help="tenant; defaults to CLEP_ORGANIZATION")
    parser.add_argument("--actor", default=os.environ.get("CLEP_ACTOR", "ci"),
                        help="who this run is attributed to in the audit trail")
    parser.add_argument("--format", choices=("text", "json", "markdown"),
                        default="text")
    sub = parser.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("gate", help="evaluate a run against a baseline")
    gate.add_argument("--project", required=True)
    gate.add_argument("--run", required=True, help="the candidate run")
    gate.add_argument("--policy", required=True, help="a published policy version")
    gate.add_argument("--baseline", required=True, help="an approved baseline")

    decision = sub.add_parser("decision", help="fetch a decision already made")
    decision.add_argument("--id", required=True)

    analysis = sub.add_parser("analysis", help="evidence for one sample")
    analysis.add_argument("--sample", required=True)
    return parser


def _fail(message: str, out) -> int:
    print(f"clep: {message}", file=out)
    return exit_codes.PLATFORM_FAILURE


def render_gate(decision: dict, fmt: str, service, organization, out) -> None:
    if fmt == "json":
        print(json.dumps(decision, indent=2, default=str), file=out)
        return
    if fmt == "markdown":
        print(service.decision_report(organization, decision["id"]), file=out)
        return
    outcome = decision["evaluatedOutcome"]
    print(f"gate: {outcome}", file=out)
    print(f"decision: {decision['id']}", file=out)
    print(f"evidence: {decision['gateEvidenceDigest']}", file=out)
    for comparison in decision.get("comparisons", []):
        # The classification and the sample size together, because a
        # classification without the sample size behind it is the "misleading
        # tiny delta" REQ-F-08-3 exists to prevent a reader from acting on.
        print(f"  {comparison['metric']}: {comparison['classification']} "
              f"(n={comparison['sampleSize']})", file=out)
        if comparison.get("abstentionReason"):
            print(f"      abstained: {comparison['abstentionReason']}", file=out)
        if comparison.get("notComparableReason"):
            print(f"      not comparable: {comparison['notComparableReason']}",
                  file=out)
    if exit_codes.blocks(outcome):
        print(f"\nthis outcome blocks the pipeline "
              f"(exit {exit_codes.for_outcome(outcome)})", file=out)


def main(argv=None, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)

    if not args.dsn:
        return _fail("no runtime DSN; pass --dsn or set CLEP_RUNTIME_DSN", err)
    if not args.organization:
        return _fail("no tenant; pass --organization or set CLEP_ORGANIZATION",
                     err)

    if args.command == "gate":
        for name, value in (("run", args.run), ("policy", args.policy),
                            ("baseline", args.baseline),
                            ("project", args.project)):
            if not is_ulid(value):
                return _fail(f"--{name} is not a well-formed identifier", err)
        service = GateService(args.dsn)
        try:
            decision = service.evaluate_gate(
                organization_id=args.organization, project_id=args.project,
                candidate_run_id=args.run, policy_version_id=args.policy,
                baseline_id=args.baseline, actor_id=args.actor)
        except Exception as e:  # noqa: BLE001 - the pipeline needs a code, not a stack
            return _fail(f"{type(e).__name__}: {e}", err)
        if decision is None:
            return _fail("no such run, policy version or baseline", err)
        render_gate(decision, args.format, service, args.organization, out)
        return exit_codes.for_outcome(decision["evaluatedOutcome"])

    if args.command == "decision":
        if not is_ulid(args.id):
            return _fail("--id is not a well-formed identifier", err)
        service = GateService(args.dsn)
        if args.format == "markdown":
            body = service.decision_report(args.organization, args.id)
            if body is None:
                return _fail("no such decision", err)
            print(body, file=out)
            return exit_codes.PASS
        decision = service.get_decision(args.organization, args.id)
        if decision is None:
            return _fail("no such decision", err)
        render_gate(decision, args.format, service, args.organization, out)
        return exit_codes.for_outcome(decision["evaluatedOutcome"])

    if args.command == "analysis":
        if not is_ulid(args.sample):
            return _fail("--sample is not a well-formed identifier", err)
        from clep.db.session import tenant_session
        from clep.rag.repository import AnalysisRepository
        with tenant_session(args.dsn, args.organization) as conn:
            body = AnalysisRepository(conn, args.organization).analysis(args.sample)
        if body is None:
            return _fail("no such sample", err)
        if args.format == "json":
            print(json.dumps(body, indent=2, default=str), file=out)
        else:
            print(f"sample: {body['runSampleId']}", file=out)
            print(f"retrieved: {len(body['retrievedContexts'])} passage(s), "
                  f"{sum(1 for c in body['retrievedContexts'] if c['cited'])} "
                  f"cited", file=out)
            missing = set(body["requiredContextRefs"]) - {
                c["contextRef"] for c in body["retrievedContexts"]}
            print(f"required but missing: {', '.join(sorted(missing)) or 'none'}",
                  file=out)
            print(f"trajectory: {len(body['trajectory'])} step(s), "
                  f"truncated={body['trajectoryTruncated']}", file=out)
            for claim in body.get("claims", []):
                print(f"  claim {claim['claimOrdinal']}: {claim['finding']}",
                      file=out)
        return exit_codes.PASS

    return _fail(f"unknown command {args.command!r}", err)  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
