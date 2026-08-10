"""What a gate outcome means to a pipeline.

`REQ-F-09-2` gives four outcomes and `REQ-F-08-4` adds a fifth state that must
never be presented as a pass. A CI job has one lever — the exit code — so the
mapping from outcome to code is a decision, and it is made here, once.

| Outcome | Code | Blocks | Why |
|---|---|---|---|
| `pass` | 0 | no | Nothing was found |
| `warning` | 0 | no | The policy chose `warning` precisely so it would not block. Turning it into a failure here would override the policy from the wrong place |
| `hard_fail` | 1 | yes | The ordinary failure code, so a job with no special handling still stops |
| `approval_required` | 75 | yes | Blocks, and is distinguishable, so a pipeline can route to a human rather than treat it as a defect |
| `insufficient_evidence` | 70 | yes | **Blocks.** The platform could not tell, and shipping on "could not tell" is the failure this product exists to prevent |
| `not_comparable` | 71 | yes | Blocks. The comparison was invalid, so there is no result to act on |
| `exception_applied` | 0 | no | A human already decided to proceed, in an audited exception that names them and expires. Blocking here would defeat the exception; the CLI says loudly that one carried the build |

The last two are the ones worth arguing about. A gate that exits 0 when it could
not measure anything would, within a week, be a gate nobody reads: teams would
see green and stop looking, and the abstention `REQ-F-08-4` fought to keep
distinct would have been converted into a pass by a shell script.

There is deliberately no flag to make an abstention pass. A team that wants to
proceed anyway records a policy exception, which is audited, expires, and names
who decided — all of which a `--ignore-abstentions` switch would discard.
"""
from __future__ import annotations

PASS = 0
HARD_FAIL = 1
INSUFFICIENT_EVIDENCE = 70
NOT_COMPARABLE = 71
APPROVAL_REQUIRED = 75
#: The platform itself failed. Distinct from every quality outcome, because
#: REQ-F-09-5 says a quality gate that reports infrastructure trouble as a
#: quality result teaches its users to ignore it.
PLATFORM_FAILURE = 78

#: Not a pass, and not a block. An exception is a recorded human decision to
#: proceed despite a finding, so the pipeline continues — but the output says
#: an exception carried it, because a build that went green on someone's
#: signature should not look like one that went green on its merits.
EXCEPTION_APPLIED = PASS

BY_OUTCOME = {
    "pass": PASS,
    "warning": PASS,
    "exception_applied": EXCEPTION_APPLIED,
    "hard_fail": HARD_FAIL,
    "approval_required": APPROVAL_REQUIRED,
    "insufficient_evidence": INSUFFICIENT_EVIDENCE,
    "not_comparable": NOT_COMPARABLE,
}


def for_outcome(outcome: str) -> int:
    """The code for a decided outcome.

    An outcome this module does not know is a platform failure rather than a
    default pass: the alternative is a new gate outcome silently shipping
    everything until someone notices.
    """
    return BY_OUTCOME.get(outcome, PLATFORM_FAILURE)


def blocks(outcome: str) -> bool:
    return for_outcome(outcome) != PASS
