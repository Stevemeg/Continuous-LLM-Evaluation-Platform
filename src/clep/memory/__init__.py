"""Historical evaluation memory — derived at read time, never a second store.

`REQ-F-AG-6` asks the platform to retain regression history, judge
disagreements, release decisions, recurring failures, drift patterns and
evaluator instability, with tenant-aware retention and deletion.

The tempting implementation is a summary table written as things happen. It is
also the wrong one: a summary that can disagree with the decisions it summarises
is worse than no summary, because it looks authoritative and is not re-derivable.
Everything here is computed from the records that already exist, under the tenant
context the caller established, so a memory query and the underlying evidence
cannot drift apart — and so erasure of the underlying content removes it from
memory too, without a second deletion path to forget about.

Retention has two layers and they do not agree, deliberately. A tenant retention
window narrows what memory reports (`REQ-F-12-6`). It cannot narrow the audit
floor: gate decisions, exceptions and escalations are governance records that
`REQ-N-COMP-3` retains independently and that the actors they record may not
delete. `retention_floor_days` is reported alongside every answer so a reader can
see which of the two produced the window they got.
"""
