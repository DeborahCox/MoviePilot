#!/usr/bin/env python3
"""Compare two pytest JUnit reports and report newly introduced failures."""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MAX_LISTED = 100


@dataclass(frozen=True)
class Report:
    failures: set[str]
    missing: bool
    exit_code: int | None


def read_report(path: Path, exit_code: int | None = None) -> Report:
    if not path.exists():
        marker = f"__pytest_exit__:{exit_code}" if exit_code is not None else "__missing_report__"
        return Report(failures={marker}, missing=True, exit_code=exit_code)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        detail = _ANSI.sub("", str(error))[:120]
        return Report(failures={f"__invalid_report__:{detail}"}, missing=True, exit_code=exit_code)

    failures: set[str] = set()
    testcases = 0
    for case in root.iter("testcase"):
        testcases += 1
        if case.find("failure") is not None or case.find("error") is not None:
            classname = case.get("classname", "")
            name = case.get("name", "")
            failures.add(f"{classname}::{name}")
    if testcases == 0:
        suite_errors = sum(
            int(suite.get("errors", "0"))
            for suite in root.iter("testsuite")
            if str(suite.get("errors", "0")).isdigit()
        )
        if suite_errors:
            failures.add(f"__suite_load_error__:{suite_errors}")
    return Report(failures=failures, missing=False, exit_code=exit_code)


def diff_reports(base: Report, head: Report) -> dict[str, list[str]]:
    return {
        "new_failures": sorted(head.failures - base.failures),
        "fixed": sorted(base.failures - head.failures),
        "persistent": sorted(base.failures & head.failures),
    }


def render_markdown(
    base_sha: str,
    head_sha: str,
    base: Report,
    head: Report,
    diff: dict[str, list[str]],
) -> str:
    lines = [
        "# Baseline test diff",
        "",
        f"- baseline: `{base_sha}`",
        f"- head: `{head_sha}`",
        f"- baseline failures: {len(base.failures)}",
        f"- head failures: {len(head.failures)}",
        f"- **new failures: {len(diff['new_failures'])}**",
        f"- fixed: {len(diff['fixed'])}",
        f"- persistent: {len(diff['persistent'])}",
        f"- reports present: baseline={'no' if base.missing else 'yes'}, head={'no' if head.missing else 'yes'}",
        "",
        "## New failures (head only)",
    ]
    lines += [f"- `{item}`" for item in diff["new_failures"][:_MAX_LISTED]] or ["- none"]
    if len(diff["new_failures"]) > _MAX_LISTED:
        lines.append(f"- ... and {len(diff['new_failures']) - _MAX_LISTED} more")
    lines += ["", "## Fixed (baseline only)"]
    lines += [f"- `{item}`" for item in diff["fixed"][:_MAX_LISTED]] or ["- none"]
    lines += ["", "## Persistent (both)"]
    lines += [f"- `{item}`" for item in diff["persistent"][:_MAX_LISTED]] or ["- none"]
    lines += ["", "This job is non-blocking in its first version; it only publishes the diff."]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-report", required=True, type=Path)
    parser.add_argument("--head-report", required=True, type=Path)
    parser.add_argument("--base-exit", type=int)
    parser.add_argument("--head-exit", type=int)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--markdown-output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = read_report(args.base_report, args.base_exit)
    head = read_report(args.head_report, args.head_exit)
    diff = diff_reports(base, head)
    markdown = render_markdown(args.base_sha, args.head_sha, base, head, diff)

    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(markdown, encoding="utf-8")
    payload = {
        "base_sha": args.base_sha,
        "head_sha": args.head_sha,
        "base_exit_code": base.exit_code,
        "head_exit_code": head.exit_code,
        "base_report_missing": base.missing,
        "head_report_missing": head.missing,
        "counts": {key: len(value) for key, value in diff.items()},
        **diff,
    }
    args.json_output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
