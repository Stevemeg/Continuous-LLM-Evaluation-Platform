"""Probabilistic judges and the ensemble that holds them to account.

Phase 8. `sdk` is one judge — its identity, its prompt, and the narrow parse that
is the second line of defence against untrusted content. `consensus` is ADR-004's
structure with ADR-017's measure: a verdict and a disagreement, or an escalation,
and never an average.

Deterministic evaluators live in `clep.evaluators` and never appear here.
`REQ-F-08-6` and I-23 require the separation to be structural, so there is no
type in this package that an evaluator result can become.
"""
