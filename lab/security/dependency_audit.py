"""Dependency audit for lab/: runs cargo-audit, pip-audit or npm audit, applies audit-ignore.toml and
decides pass or fail. Used by .github/workflows/dependency-audit.yml; standard library only.

Fail rule:
- cargo: a vulnerability whose CVSS v3 score is high (>= 7.0) or critical, or that has no usable CVSS
  (RustSec often has none; unknown is treated as high), and every "unsound" advisory (coordinator
  decision 2026-10-12: no known problem stays silent). Unmaintained and yanked warnings are printed,
  never fatal.
- pip:   every finding (pip-audit reports no severity, so each one counts as unknown).
- npm:   advisories with severity high or critical, as `npm audit --omit=dev --audit-level=high`.
An ignore entry (with reason, evidence that no compatible patched version exists, date and review
date) turns a failing finding into a printed "ignored" line. A tool error or unreadable output always fails.

Usage:
  dependency_audit.py cargo LOCKFILE... [--optional LOCKFILE]...
  dependency_audit.py pip REQUIREMENTS [--tool PATH-TO-pip-audit]
  dependency_audit.py npm DIR
Exit: 0 pass, 1 failing findings, 2 tool or input error.
"""
import argparse
import datetime
import json
import math
import os
import re
import subprocess
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
IGNORE_FILE = os.path.join(HERE, "audit-ignore.toml")
IGNORE_FIELDS = ("id", "ecosystem", "package", "reason", "risk", "evidence", "approved", "approved_by", "review_by")
OPTIONAL_FIELDS = ("version",)  # when set, the entry covers only that exact version
FAILING = {"high", "critical", "unknown", "unsound"}


class AuditError(Exception):
    pass


# --- severity -------------------------------------------------------------------------------------

_V3 = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
    "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62},
    "C": {"H": 0.56, "L": 0.22, "N": 0.0},
    "I": {"H": 0.56, "L": 0.22, "N": 0.0},
    "A": {"H": 0.56, "L": 0.22, "N": 0.0},
}


def _roundup(x):
    """CVSS v3.1 Roundup: smallest one-decimal number >= x, robust to float noise."""
    i = round(x * 100000)
    return i / 100000.0 if i % 10000 == 0 else (math.floor(i / 10000) + 1) / 10.0


def cvss3_score(vector):
    """Base score of a CVSS:3.0/3.1 vector, or None for anything else (v2, v4, malformed)."""
    if not vector or not re.match(r"^CVSS:3\.[01]/", vector):
        return None
    m = dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)
    try:
        scope_changed = {"U": False, "C": True}[m["S"]]
        pr = {"N": 0.85, "L": 0.68 if scope_changed else 0.62, "H": 0.5 if scope_changed else 0.27}[m["PR"]]
        av, ac, ui = _V3["AV"][m["AV"]], _V3["AC"][m["AC"]], _V3["UI"][m["UI"]]
        c, i, a = _V3["C"][m["C"]], _V3["I"][m["I"]], _V3["A"][m["A"]]
    except KeyError:
        return None
    iss = 1 - (1 - c) * (1 - i) * (1 - a)
    impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if scope_changed else 6.42 * iss
    if impact <= 0:
        return 0.0
    exploit = 8.22 * av * ac * pr * ui
    total = 1.08 * (impact + exploit) if scope_changed else impact + exploit
    return _roundup(min(total, 10.0))


def severity_from_score(score):
    if score is None:
        return "unknown"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low" if score > 0 else "none"


# --- ignore file ----------------------------------------------------------------------------------

def load_ignores(path=IGNORE_FILE):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    entries = data.get("ignore", [])
    if set(data) - {"ignore"} or not isinstance(entries, list):
        raise AuditError(f"{path}: only [[ignore]] tables are allowed")
    for n, e in enumerate(entries, 1):
        missing = [k for k in IGNORE_FIELDS if not str(e.get(k, "")).strip()]
        extra = sorted(set(e) - set(IGNORE_FIELDS) - set(OPTIONAL_FIELDS))
        if missing or extra:
            raise AuditError(f"{path}: entry {n} ({e.get('id', '?')}): missing {missing} unknown {extra}")
        if e["ecosystem"] not in ("cargo", "pip", "npm"):
            raise AuditError(f"{path}: entry {n}: ecosystem must be cargo, pip or npm")
        for k in ("approved", "review_by"):
            try:
                datetime.date.fromisoformat(str(e[k]))
            except ValueError:
                raise AuditError(f"{path}: entry {n}: {k} must be YYYY-MM-DD") from None
    return entries


def pep503(name):
    """PyPI's normalized project name (PEP 503): typing_extensions == Typing.Extensions == typing-extensions."""
    return re.sub(r"[-_.]+", "-", name).lower()


def match_ignore(finding, ignores, ecosystem):
    ids = {finding["id"], *finding.get("aliases", [])}
    same = (lambda a, b: pep503(a) == pep503(b)) if ecosystem == "pip" else (lambda a, b: a == b)
    for e in ignores:
        if (e["ecosystem"] == ecosystem and same(e["package"], finding["package"]) and e["id"] in ids
                and e.get("version", finding["version"]) == finding["version"]):
            return e
    return None


# --- parsers: tool JSON -> findings ----------------------------------------------------------------

def parse_cargo(report):
    """cargo-audit --json -> (vulnerabilities, warnings)."""
    try:
        vulns = report["vulnerabilities"]["list"]
        warn_groups = report.get("warnings") or {}
    except (KeyError, TypeError):
        raise AuditError("cargo-audit output has no vulnerabilities.list") from None
    findings = []
    for v in vulns:
        adv, pkg = v["advisory"], v["package"]
        score = cvss3_score(adv.get("cvss"))
        findings.append({
            "id": adv["id"], "aliases": adv.get("aliases") or [], "package": pkg["name"],
            "version": pkg["version"], "title": adv.get("title", ""),
            "severity": severity_from_score(score), "score": score,
        })
    warnings = []
    for kind, items in warn_groups.items():
        for w in items or []:
            adv = w.get("advisory") or {}
            if kind == "unsound":
                findings.append({
                    "id": adv["id"], "aliases": adv.get("aliases") or [], "package": w["package"]["name"],
                    "version": w["package"]["version"], "title": adv.get("title", ""),
                    "severity": "unsound", "score": None,
                })
                continue
            warnings.append({"kind": kind, "id": adv.get("id", "-"), "package": w["package"]["name"],
                             "version": w["package"]["version"], "title": adv.get("title", kind)})
    return findings, warnings


def parse_pip(report):
    try:
        deps = report["dependencies"]
    except (KeyError, TypeError):
        raise AuditError("pip-audit output has no dependencies") from None
    findings = []
    for d in deps:
        if "skip_reason" in d:
            raise AuditError(f"pip-audit skipped {d.get('name')}: {d['skip_reason']}")
        for v in d.get("vulns", []):
            findings.append({
                "id": v["id"], "aliases": v.get("aliases") or [], "package": pep503(d["name"]),
                "version": d["version"], "title": (v.get("description") or "").split("\n")[0][:120],
                "severity": "unknown", "score": None,
            })
    return findings


def parse_npm(report):
    if "error" in report:
        raise AuditError(f"npm audit error: {report['error']}")
    try:
        vulns = report["vulnerabilities"]
    except (KeyError, TypeError):
        raise AuditError("npm audit output has no vulnerabilities") from None
    seen, findings = set(), []
    for entry in vulns.values():
        for via in entry.get("via", []):
            if not isinstance(via, dict):
                continue  # a transitive pointer; the advisory itself is listed under its own package
            url = via.get("url") or ""
            adv_id = url.rstrip("/").rsplit("/", 1)[-1] if "/advisories/" in url else str(via.get("source"))
            key = (adv_id, via.get("name"))
            if key in seen:
                continue
            seen.add(key)
            sev = via.get("severity", "unknown")
            findings.append({
                "id": adv_id, "aliases": [], "package": via.get("name", entry.get("name")),
                "version": via.get("range", ""), "title": via.get("title", ""),
                "severity": sev if sev in ("info", "low", "moderate", "high", "critical") else "unknown",
                "score": (via.get("cvss") or {}).get("score"),
            })
    return findings


# --- decide ---------------------------------------------------------------------------------------

def decide(findings, ignores, ecosystem, today):
    """Return (failing, ignored, informational, expired) lists of findings."""
    failing, ignored, info, expired = [], [], [], []
    for f in findings:
        if f["severity"] not in FAILING:
            info.append(f)
            continue
        e = match_ignore(f, ignores, ecosystem)
        if e is None:
            failing.append(f)
        elif datetime.date.fromisoformat(str(e["review_by"])) < today:
            expired.append((f, e))
            failing.append(f)
        else:
            ignored.append((f, e))
    return failing, ignored, info, expired


def stale_ignores(findings, ignores, ecosystem):
    used = {id(match_ignore(f, ignores, ecosystem)) for f in findings}
    return [e for e in ignores if e["ecosystem"] == ecosystem and id(e) not in used]


def _line(f):
    score = f" {f['score']}" if f.get("score") is not None else ""
    return f"{f['id']} {f['package']} {f['version']} [{f['severity']}{score}] {f['title']}"


def report(label, ecosystem, findings, ignores, warnings=(), today=None, out=sys.stdout):
    today = today or datetime.date.today()
    failing, ignored, info, expired = decide(findings, ignores, ecosystem, today)
    p = lambda s="": print(s, file=out)  # noqa: E731
    p(f"== {label}: {len(findings)} advisories, {len(failing)} failing, {len(ignored)} ignored, "
      f"{len(info)} below threshold, {len(warnings)} warnings")
    for f in failing:
        p(f"::error title=dependency audit ({ecosystem})::{_line(f)}")
    for f, e in expired:
        p(f"::error title=ignore expired::{f['id']} {f['package']}: review_by {e['review_by']} has passed")
    for f, e in ignored:
        p(f"  ignored  {_line(f)}\n           reason: {e['reason']}\n           risk:   {e['risk']}"
          f"\n           (approved {e['approved']} by {e['approved_by']}, review by {e['review_by']})")
    for f in info:
        p(f"  info     {_line(f)}")
    for w in warnings:
        p(f"  warning  {w['kind']}: {w['id']} {w['package']} {w['version']} {w['title']}")
    return failing


# --- tool runners ---------------------------------------------------------------------------------

def run_json(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AuditError(f"{' '.join(cmd)} exited {proc.returncode} without JSON output") from None


def check_pinned(path):
    """pip-audit runs with --no-deps, which is only sound when every package is pinned exactly."""
    names = []
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!-]+)", line)
            if not m:
                raise AuditError(f"{path}: '{line}' is not an exact name==version pin")
            names.append(pep503(m.group(1)))
    if not names:
        raise AuditError(f"{path}: no requirements")
    return names


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="ecosystem", required=True)
    c = sub.add_parser("cargo")
    c.add_argument("lockfiles", nargs="*")
    c.add_argument("--optional", action="append", default=[], help="lockfile that may not exist yet")
    p = sub.add_parser("pip")
    p.add_argument("requirements")
    p.add_argument("--tool", default="pip-audit")
    n = sub.add_parser("npm")
    n.add_argument("dir")
    a = ap.parse_args(argv)

    try:
        ignores = load_ignores()
        failing, findings_all = [], []
        if a.ecosystem == "cargo":
            locks = list(a.lockfiles)
            for opt in a.optional:
                if os.path.exists(opt):
                    locks.append(opt)
                else:
                    print(f"::notice title=dependency audit (cargo)::{opt} does not exist yet; skipped")
            if not locks:
                raise AuditError("no lockfiles to audit")
            for lock in locks:
                if not os.path.exists(lock):
                    raise AuditError(f"{lock} does not exist")
                findings, warnings = parse_cargo(run_json(["cargo", "audit", "--json", "--file", lock]))
                findings_all += findings
                failing += report(lock, "cargo", findings, ignores, warnings)
        elif a.ecosystem == "pip":
            check_pinned(a.requirements)
            cmd = [a.tool, "-r", a.requirements, "--no-deps", "--disable-pip", "--format", "json",
                   "--progress-spinner", "off"]
            findings_all = parse_pip(run_json(cmd))
            failing = report(a.requirements, "pip", findings_all, ignores)
        else:
            findings_all = parse_npm(run_json(["npm", "audit", "--omit=dev", "--json"], cwd=a.dir))
            failing = report(a.dir, "npm", findings_all, ignores)
        for e in stale_ignores(findings_all, ignores, a.ecosystem):
            print(f"::warning title=stale ignore::{e['id']} {e['package']} in audit-ignore.toml matched nothing")
    except (AuditError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"::error title=dependency audit ({a.ecosystem})::{exc}")
        return 2
    if failing:
        print(f"FAIL: {len(failing)} high, critical, unknown-severity or unsound advisories without a valid ignore entry")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
