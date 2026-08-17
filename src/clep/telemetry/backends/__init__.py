"""Optional telemetry backends.

Nothing in this package is imported by the core. A module here may import a
third-party package, and every one of them does so **inside a function**, so that
importing `clep.telemetry` — or `clep`, or the application — never requires a
telemetry dependency to be installed. That is what makes the default build of
this project the adapter-excluded build ADR-009 rule 3 asks to be validated,
rather than a special configuration somebody exercises occasionally.
"""
