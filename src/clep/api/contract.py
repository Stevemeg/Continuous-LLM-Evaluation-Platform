"""The OpenAPI contract, treated as authoritative.

The contract is not generated from this application and must never be. It is
read from `docs/api/openapi.json`, and the application is checked against it.
That direction is the whole point: a contract generated from an implementation
records whatever the implementation happens to do, and can never disagree with
it, which makes it useless as a specification.

`operation_for` is how a handler declares which operation it implements. If the
path or method does not appear in the contract, the application fails to start —
loudly, at import time, rather than at the first request from a client that had
been promised something else.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONTRACT_RELATIVE = Path("docs") / "api" / "openapi.json"


class ContractError(RuntimeError):
    pass


def repository_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONTRACT_RELATIVE).exists():
            return candidate
    raise ContractError(
        f"could not locate {CONTRACT_RELATIVE} above {here}; the contract is "
        f"authoritative and the application will not start without it")


@lru_cache(maxsize=1)
def load(root: str | None = None) -> dict:
    base = Path(root) if root else repository_root()
    return json.loads((base / CONTRACT_RELATIVE).read_text(encoding="utf-8"))


def operations(root: str | None = None) -> dict[tuple[str, str], dict]:
    """Every operation the contract declares, keyed by (METHOD, path)."""
    spec = load(root)
    found = {}
    for path, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                found[(method.upper(), path)] = operation
    return found


def operation_for(method: str, path: str, root: str | None = None) -> dict:
    key = (method.upper(), path)
    declared = operations(root)
    if key not in declared:
        raise ContractError(
            f"{method.upper()} {path} is not in the contract. Add it to "
            f"docs/api/openapi.json first: the contract leads and the "
            f"implementation follows, never the other way round.")
    return declared[key]


def operation_id(method: str, path: str, root: str | None = None) -> str:
    return operation_for(method, path, root)["operationId"]


def requirements_of(method: str, path: str, root: str | None = None) -> list[str]:
    return list(operation_for(method, path, root).get("x-requirements", []))


def schema(name: str, root: str | None = None) -> dict:
    schemas = load(root)["components"]["schemas"]
    if name not in schemas:
        raise ContractError(f"the contract declares no schema named {name!r}")
    return schemas[name]


def enum_of(name: str, root: str | None = None) -> list[str]:
    """Read a vocabulary from the contract rather than restating it in code.

    Every enum restated in Python is a second definition of one vocabulary, and
    the two drift. Phase 4 caught that between the schema and the contract; the
    same rule applies to the application.
    """
    declared = schema(name, root)
    if "enum" not in declared:
        raise ContractError(f"schema {name!r} declares no enum")
    return list(declared["enum"])
