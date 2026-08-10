"""The command line a CI job runs.

Canonical §11 asks for a CLI and API suitable for GitHub Actions and other CI
systems, evaluating a change against an approved baseline and reporting in both
machine-readable and human-readable form.

The whole surface is thin on purpose. It parses arguments, calls the same
service the HTTP API calls, renders what comes back, and chooses an exit code.
It contains no evaluation logic of its own, because a CLI that decided anything
would be a second decision path — and the two would disagree eventually, with
the pipeline believing whichever one it happened to call.
"""
