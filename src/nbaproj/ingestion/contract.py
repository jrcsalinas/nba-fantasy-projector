"""Helpers for the manual payload contract checks in ``scripts/``."""

from collections.abc import Mapping, Sequence


def missing_fields(container: Mapping, fields: Sequence[str], where: str) -> list[str]:
    """Names of ``fields`` absent from ``container``, each prefixed with ``where``."""
    return [f"{where}.{field}" for field in fields if field not in container]


def report(problems: Sequence[str]) -> int:
    """Print each missing field and a RESULT line. Returns the exit code (1 if any)."""
    for problem in problems:
        print(f"  MISSING {problem}")
    print("RESULT:", "OK" if not problems else "FIELDS MISSING")
    return 1 if problems else 0
