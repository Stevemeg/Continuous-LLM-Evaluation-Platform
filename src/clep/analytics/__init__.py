"""Analytics: figures derived from the rows that already hold the answer.

`CAP-11` is a reporting capability, and the decision that shapes every module
here is that **nothing is stored**. There is no aggregate table, no rollup, no
nightly job that writes a number a dashboard later reads. Every figure is
computed, on read, from `run_sample`, `evaluator_outcome`, `sample_cost`,
`consensus_result` and `trajectory_step`.

That is `REQ-F-11-6` made structural rather than promised. A stored aggregate is
a figure whose provenance is a previous computation; asked "which samples
produced this", it can only answer "the ones that were there when the job ran".
Every figure this package returns carries the runs it was computed from and the
number of samples behind it, because it has just read them.

`REQ-F-11-7` is the other half, and it is why `Completeness` is a type rather
than a flag on some responses. A mean over a run that was cancelled halfway is
not a mean of that run; it is a mean of the part that happened, and a reader
comparing it with last week's is being misled unless something says so. Every
figure is marked, in every view and export it appears in — including the
executive scorecard, which is exactly where such a qualification would otherwise
be dropped for being untidy.
"""
from clep.analytics.completeness import (COMPLETE, INCOMPLETE, Completeness,
                                         completeness_of)

__all__ = ["Completeness", "completeness_of", "COMPLETE", "INCOMPLETE"]
