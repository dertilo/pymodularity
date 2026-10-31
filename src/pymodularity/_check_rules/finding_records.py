"""The shared `Finding` record `pymodularity check --output-format json`
prints: 1 flat list fed by every rule's own module (`cycle_detection`,
`privacy_findings`, `declared_deps`, `structure_limits`), each converting
its own finding type into `Finding` itself, since each owns the private
dataclass a conversion needs to read. Text mode never touches this module
-- it prints straight from each rule's own finding type, as before."""

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_REPO_URL = "https://github.com/dertilo/pymodularity/blob/main"


@dataclass(frozen=True, slots=True)
class Finding:
    """One check finding, rule-agnostic: the shape `--output-format json`
    serializes, 1 object per array element."""

    rule: str
    severity: str
    path: str
    line: int
    col: int
    message: str
    url: str

    def to_dict(self) -> Mapping[str, object]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "message": self.message,
            "url": self.url,
        }


def make_finding(
    rule: str,
    path: Path,
    message: str,
    url_anchor: str,
    *,
    line: int = 0,
    col: int = 0,
) -> Finding:
    return Finding(
        rule=rule,
        severity="error",
        path=path.as_posix(),
        line=line,
        col=col,
        message=message,
        url=f"{_REPO_URL}/{url_anchor}",
    )


def print_findings_json(findings: Sequence[Finding]) -> None:
    """Prints exactly 1 JSON array, once, at the end -- no streaming, no
    other stdout output in json mode."""
    print(json.dumps([f.to_dict() for f in findings]))


def print_uncaught_exception() -> None:
    """json mode's exit-2 path: the traceback goes to stderr, stdout stays
    empty because the array is only ever printed after every finding is
    collected, never before."""
    import traceback

    traceback.print_exc(file=sys.stderr)
