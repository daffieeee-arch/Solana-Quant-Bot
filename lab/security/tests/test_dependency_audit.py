"""Tests for lab/security/dependency_audit.py: CVSS scoring, the fail rule per ecosystem, ignore entries
(reason, dates, aliases, expiry) and the pinned-requirements guard. No network; stdlib only.

Run: python3 lab/security/tests/test_dependency_audit.py
"""
import datetime
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dependency_audit as da  # noqa: E402

TODAY = datetime.date(2026, 10, 12)


def ignore(**kw):
    e = {"id": "RUSTSEC-2099-0001", "ecosystem": "cargo", "package": "foo", "reason": "test", "risk": "none", "evidence": "none compatible",
         "approved": "2026-10-12", "approved_by": "test", "review_by": "2027-01-12"}
    e.update(kw)
    return e


def cargo_vuln(id_, pkg, cvss=None, aliases=()):
    return {"advisory": {"id": id_, "aliases": list(aliases), "title": "t", "cvss": cvss},
            "package": {"name": pkg, "version": "1.0.0"}}


def cargo_report(*vulns, warnings=None):
    return {"vulnerabilities": {"found": bool(vulns), "count": len(vulns), "list": list(vulns)},
            "warnings": warnings or {}}


def run_report(findings, ignores, eco, today=TODAY):
    out = io.StringIO()
    failing = da.report("x", eco, findings, ignores, today=today, out=out)
    return failing, out.getvalue()


class Cvss(unittest.TestCase):
    def test_known_scores(self):
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), 9.8)
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"), 10.0)
        self.assertEqual(da.cvss3_score("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H"), 7.5)
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N"), 6.4)
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:N/A:N"), 3.1)
        # the Agave-pinned curve25519-dalek and ed25519-dalek advisories as GitHub vectors them
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:L/AC:H/PR:N/UI:N/S:U/C:L/I:N/A:N"), 2.9)
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:L/AC:H/PR:N/UI:N/S:C/C:H/I:N/A:N"), 5.9)
        self.assertEqual(da.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N"), 0.0)

    def test_unscoreable_is_unknown(self):
        for v in (None, "", "CVSS:4.0/AV:N/AC:H/AT:N/PR:N/UI:N/VC:L/VI:L/VA:N/SC:N/SI:N/SA:N",
                  "AV:N/AC:L/Au:N/C:P/I:P/A:P", "CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"):
            self.assertIsNone(da.cvss3_score(v))
            self.assertEqual(da.severity_from_score(da.cvss3_score(v)), "unknown")

    def test_bands(self):
        self.assertEqual([da.severity_from_score(s) for s in (0.0, 3.9, 4.0, 6.9, 7.0, 8.9, 9.0)],
                         ["none", "low", "medium", "medium", "high", "high", "critical"])


class Cargo(unittest.TestCase):
    def test_fail_rule(self):
        findings, warnings = da.parse_cargo(cargo_report(
            cargo_vuln("RUSTSEC-1", "crit", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),
            cargo_vuln("RUSTSEC-2", "med", "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N"),
            cargo_vuln("RUSTSEC-3", "nocvss"),
            warnings={"unmaintained": [{"advisory": {"id": "RUSTSEC-4", "title": "gone"},
                                        "package": {"name": "old", "version": "0.1.0"}}],
                      "unsound": [{"kind": "unsound", "advisory": {"id": "RUSTSEC-5", "title": "ub", "aliases": ["GHSA-u"]},
                                   "package": {"name": "unsafe", "version": "0.7.3"}}],
                      "yanked": [{"advisory": None, "package": {"name": "y", "version": "0.2.0"}}]}))
        failing, out = run_report(findings, [], "cargo")
        self.assertEqual(sorted(f["package"] for f in failing), ["crit", "nocvss", "unsafe"])
        self.assertEqual(len(warnings), 2)
        self.assertIn("::error", out)
        self.assertIn("info     RUSTSEC-2 med", out)

    def test_unsound_can_be_ignored_like_a_vulnerability(self):
        findings, warnings = da.parse_cargo(cargo_report(warnings={"unsound": [
            {"advisory": {"id": "RUSTSEC-2099-0001", "title": "ub"}, "package": {"name": "foo", "version": "1.0.0"}}]}))
        self.assertEqual((len(findings), warnings), (1, []))
        self.assertEqual(len(run_report(findings, [], "cargo")[0]), 1)
        failing, out = run_report(findings, [ignore()], "cargo")
        self.assertEqual(failing, [])
        self.assertIn("ignored  RUSTSEC-2099-0001 foo 1.0.0 [unsound]", out)

    def test_ignore_by_alias_is_printed_with_reason(self):
        findings, _ = da.parse_cargo(cargo_report(cargo_vuln("RUSTSEC-2099-0002", "foo", aliases=["GHSA-aaaa"])))
        failing, out = run_report(findings, [ignore(id="GHSA-aaaa", reason="no signing")], "cargo")
        self.assertEqual(failing, [])
        self.assertIn("ignored  RUSTSEC-2099-0002 foo", out)
        self.assertIn("reason: no signing", out)

    def test_ignore_must_match_package_and_ecosystem(self):
        findings, _ = da.parse_cargo(cargo_report(cargo_vuln("RUSTSEC-2099-0001", "foo")))
        self.assertEqual(len(run_report(findings, [ignore(package="bar")], "cargo")[0]), 1)
        self.assertEqual(len(run_report(findings, [ignore(ecosystem="npm")], "cargo")[0]), 1)

    def test_version_pinned_ignore_covers_only_that_version(self):
        findings, _ = da.parse_cargo(cargo_report(warnings={"unsound": [
            {"advisory": {"id": "RUSTSEC-2099-0001", "title": "ub"}, "package": {"name": "foo", "version": v}}
            for v in ("0.5.10", "0.9.10")]}))
        failing, _ = run_report(findings, [ignore(version="0.5.10")], "cargo")
        self.assertEqual([f["version"] for f in failing], ["0.9.10"])

    def test_expired_ignore_fails_again(self):
        findings, _ = da.parse_cargo(cargo_report(cargo_vuln("RUSTSEC-2099-0001", "foo")))
        failing, out = run_report(findings, [ignore(review_by="2026-10-11")], "cargo")
        self.assertEqual(len(failing), 1)
        self.assertIn("ignore expired", out)

    def test_stale_ignore_reported(self):
        stale = da.stale_ignores([], [ignore()], "cargo")
        self.assertEqual([e["id"] for e in stale], ["RUSTSEC-2099-0001"])

    def test_bad_output_is_an_error(self):
        with self.assertRaises(da.AuditError):
            da.parse_cargo({"database": {}})


class Pip(unittest.TestCase):
    def test_every_finding_fails(self):
        findings = da.parse_pip({"dependencies": [
            {"name": "PyYAML", "version": "5.3", "vulns": [{"id": "PYSEC-1", "aliases": ["CVE-1"], "description": "x"}]},
            {"name": "numpy", "version": "2.5.3", "vulns": []}]})
        failing, _ = run_report(findings, [], "pip")
        self.assertEqual([(f["package"], f["severity"]) for f in failing], [("pyyaml", "unknown")])
        ok, _ = run_report(findings, [ignore(id="CVE-1", ecosystem="pip", package="pyyaml")], "pip")
        self.assertEqual(ok, [])

    def test_names_normalized_per_pep503(self):
        findings = da.parse_pip({"dependencies": [
            {"name": "Typing_Extensions", "version": "4.0", "vulns": [{"id": "PYSEC-2", "aliases": [], "description": "x"}]}]})
        self.assertEqual(findings[0]["package"], "typing-extensions")
        for name in ("typing_extensions", "Typing.Extensions", "typing-extensions"):
            ok, _ = run_report(findings, [ignore(id="PYSEC-2", ecosystem="pip", package=name)], "pip")
            self.assertEqual(ok, [], name)

    def test_skipped_dependency_is_an_error(self):
        with self.assertRaises(da.AuditError):
            da.parse_pip({"dependencies": [{"name": "x", "skip_reason": "not on PyPI"}]})

    def test_requirements_must_be_pinned(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# c\nduckdb==1.5.5  # pinned\nnumpy>=2\n")
        try:
            with self.assertRaises(da.AuditError):
                da.check_pinned(f.name)
        finally:
            os.unlink(f.name)

    def test_repo_requirements_are_pinned(self):
        req = os.path.join(da.HERE, "..", "requirements-lab.txt")
        self.assertIn("duckdb", da.check_pinned(req))


class Npm(unittest.TestCase):
    REPORT = {"vulnerabilities": {
        "a": {"name": "a", "severity": "high", "via": [
            {"source": 1, "name": "a", "title": "bad", "url": "https://github.com/advisories/GHSA-hhhh", "severity": "high", "range": "<2"}]},
        "b": {"name": "b", "severity": "high", "via": ["a"]},
        "c": {"name": "c", "severity": "moderate", "via": [
            {"source": 2, "name": "c", "title": "meh", "url": "https://github.com/advisories/GHSA-mmmm", "severity": "moderate", "range": "<1"}]},
    }}

    def test_audit_level_high(self):
        findings = da.parse_npm(self.REPORT)
        self.assertEqual(sorted(f["id"] for f in findings), ["GHSA-hhhh", "GHSA-mmmm"])
        failing, _ = run_report(findings, [], "npm")
        self.assertEqual([f["id"] for f in failing], ["GHSA-hhhh"])
        ok, _ = run_report(findings, [ignore(id="GHSA-hhhh", ecosystem="npm", package="a")], "npm")
        self.assertEqual(ok, [])

    def test_npm_error_is_an_error(self):
        with self.assertRaises(da.AuditError):
            da.parse_npm({"error": {"code": "ENOLOCK"}})


class IgnoreFile(unittest.TestCase):
    def write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_repo_ignore_file_is_valid(self):
        entries = da.load_ignores()
        self.assertTrue(entries)
        for e in entries:
            self.assertGreater(datetime.date.fromisoformat(e["review_by"]), datetime.date.fromisoformat(e["approved"]))

    def test_missing_reason_rejected(self):
        path = self.write('[[ignore]]\nid="X"\necosystem="npm"\npackage="a"\nrisk="k"\nevidence="e"\napproved="2026-10-12"\n'
                          'approved_by="me"\nreview_by="2027-01-01"\n')
        with self.assertRaisesRegex(da.AuditError, "reason"):
            da.load_ignores(path)

    def test_missing_evidence_rejected(self):
        path = self.write('[[ignore]]\nid="X"\necosystem="npm"\npackage="a"\nreason="r"\nrisk="k"\napproved="2026-10-12"\n'
                          'approved_by="me"\nreview_by="2027-01-01"\n')
        with self.assertRaisesRegex(da.AuditError, r"missing \['evidence'\]"):
            da.load_ignores(path)

    def test_missing_risk_rejected(self):
        path = self.write('[[ignore]]\nid="X"\necosystem="npm"\npackage="a"\nreason="r"\nevidence="e"\n'
                          'approved="2026-10-12"\napproved_by="me"\nreview_by="2027-01-01"\n')
        with self.assertRaisesRegex(da.AuditError, r"missing \['risk'\]"):
            da.load_ignores(path)

    def test_bad_date_and_unknown_keys_rejected(self):
        base = 'id="X"\necosystem="npm"\npackage="a"\nreason="r"\nrisk="k"\nevidence="e"\napproved_by="me"\n'
        with self.assertRaises(da.AuditError):
            da.load_ignores(self.write(f'[[ignore]]\n{base}approved="12-10-2026"\nreview_by="2027-01-01"\n'))
        with self.assertRaises(da.AuditError):
            da.load_ignores(self.write(f'[[ignore]]\n{base}approved="2026-10-12"\nreview_by="2027-01-01"\nsilent=true\n'))
        with self.assertRaises(da.AuditError):
            da.load_ignores(self.write('severity = "low"\n'))


if __name__ == "__main__":
    unittest.main(verbosity=1)
