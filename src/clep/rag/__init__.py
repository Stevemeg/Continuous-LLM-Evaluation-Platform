"""RAG evaluation: hallucination analysis and stage attribution.

Phase 9. The deterministic RAG evaluators live in `clep.evaluators.rag`; this
package holds the two analyses that combine several signals into a statement
about *what went wrong*, which is a different job from scoring.

Neither analysis invents a number. Both read judgements and deterministic
outcomes that already exist, and both report `not_analysable` with a reason
rather than guessing when an input they need is missing.
"""
