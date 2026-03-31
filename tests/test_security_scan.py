"""
tests/test_security_scan.py — Automated security scanning.

Layer 2: pip-audit checks installed packages against the Python advisory
         database for known CVEs.
Layer 3: bandit performs static analysis on Python source for insecure
         code patterns (medium + high severity only).

These tests have no dependency on a running server.

Run:
    pytest tests/test_security_scan.py -v
"""
import json
import subprocess
import sys
from pathlib import Path

# Root of the project (one level up from tests/)
PROJECT_ROOT = str(Path(__file__).parent.parent)


def test_no_dependency_cves():
    """All installed packages must be free of known CVEs (via pip-audit).

    If this test fails, a CVE has been disclosed in one of the project's
    dependencies. Upgrade the affected package to the fixed version shown.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    # pip-audit exits non-zero when vulnerabilities are found
    if result.returncode == 0:
        return  # clean — no CVEs

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"pip-audit failed to produce JSON output.\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )

    # Collect all vulnerabilities across all packages
    vulns = []
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            pkg = dep.get("name", "unknown")
            ver = dep.get("version", "?")
            cve_id = vuln.get("id", "unknown")
            fix = vuln.get("fix_versions", [])
            fix_str = f"fix: upgrade to {fix[0]}" if fix else "no fix version listed"
            vulns.append(f"  {pkg} {ver} — {cve_id} ({fix_str})")

    if vulns:
        raise AssertionError(
            f"FAIL: {len(vulns)} CVE(s) found in dependencies:\n" + "\n".join(vulns)
        )


def test_no_high_severity_code_issues():
    """Python source must have no medium or high severity issues (via bandit).

    bandit flags: -ll = medium severity and above (skips low-severity noise)
    Excludes tests/ and docs/ to avoid false positives from test scaffolding.

    If this test fails, a security issue was introduced in the source code.
    Fix the flagged file/line before merging.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "bandit",
            "-r", PROJECT_ROOT,
            "-ll",
            "--format", "json",
            "--exclude", f"{PROJECT_ROOT}/tests,{PROJECT_ROOT}/docs,{PROJECT_ROOT}/venv",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode == 0:
        return  # clean — no issues

    # bandit may prefix stdout with a Rich progress bar before the JSON object;
    # strip any leading non-JSON characters before parsing.
    stdout = result.stdout
    json_start = stdout.find("{")
    if json_start > 0:
        stdout = stdout[json_start:]

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        raise AssertionError(
            f"bandit failed to produce JSON output.\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )

    issues = data.get("results", [])
    if not issues:
        return  # bandit exited non-zero but no issues in JSON (e.g., config warning)

    lines = []
    for issue in issues:
        filename = issue.get("filename", "?").replace(PROJECT_ROOT + "/", "")
        lineno   = issue.get("line_number", "?")
        severity = issue.get("issue_severity", "?")
        text     = issue.get("issue_text", "?")
        lines.append(f"  {filename}:{lineno} [{severity}] {text}")

    raise AssertionError(
        f"FAIL: {len(issues)} bandit issue(s) found:\n" + "\n".join(lines)
    )
