"""Reasoning components, and the bounds they run inside.

Phase 8. ADR-002 chose a project-owned orchestration layer over a framework, so
the bounds `REQ-F-AG-5` requires are code in `sdk` rather than configuration of a
dependency. Canonical §7 governs what belongs here at all: reasoning where
reasoning adds value, and conventional software everywhere else. Validation,
persistence and arithmetic are not in this package.
"""
