"""Tests for scripts/harness.py.

A script without a test is a script that silently breaks when the data shape or
an external contract changes. These tests pin the feature-table parser, the
state queries, the evidence extractor, and the live workspace validation so
regressions surface here before the harness lets a bad change through.

Run: python3 -m unittest scripts.test_harness
"""
import unittest

from scripts import harness

SAMPLE_FEATURES = (
    "intro text\n"
    f"{harness.START_MARKER}\n"
    "| id | behavior | verification | state | evidence |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| H001 | Harness files exist | `python3 scripts/harness.py check --skip-tests` | active | Awaiting verification |\n"
    "| MDP001 | First app feature exists | `false` | not_started | Replace later |\n"
    f"{harness.END_MARKER}\n"
    "trailing text\n"
)


class ParseTests(unittest.TestCase):
    def test_parses_rows(self):
        feats = harness.parse_features_text(SAMPLE_FEATURES)
        self.assertEqual([f.id for f in feats], ["H001", "MDP001"])

    def test_header_and_separator_skipped(self):
        feats = harness.parse_features_text(SAMPLE_FEATURES)
        self.assertEqual(len(feats), 2)

    def test_missing_markers_raises(self):
        with self.assertRaises(ValueError):
            harness.parse_features_text("no markers anywhere")

    def test_round_trip_render(self):
        feats = harness.parse_features_text(SAMPLE_FEATURES)
        rendered = harness.render_features(feats)
        wrapped = f"{harness.START_MARKER}\n{rendered}\n{harness.END_MARKER}"
        reparsed = harness.parse_features_text(wrapped)
        self.assertEqual(
            [(f.id, f.state) for f in reparsed],
            [("H001", "active"), ("MDP001", "not_started")],
        )


class QueryTests(unittest.TestCase):
    def test_next_feature_prefers_active(self):
        feats = harness.parse_features_text(SAMPLE_FEATURES)
        self.assertEqual(harness.next_feature(feats).id, "H001")

    def test_find_feature(self):
        feats = harness.parse_features_text(SAMPLE_FEATURES)
        self.assertEqual(harness.find_feature(feats, "MDP001").behavior,
                         "First app feature exists")
        self.assertIsNone(harness.find_feature(feats, "NOPE000"))


class SignalTests(unittest.TestCase):
    def test_picks_last_matching_line(self):
        out = "compiling\nall checks passed\nthen noise"
        self.assertEqual(harness.evidence_signal(out), "all checks passed")

    def test_no_match_returns_empty(self):
        self.assertEqual(harness.evidence_signal("nothing relevant here"), "")


class ValidateTests(unittest.TestCase):
    def test_live_workspace_is_clean(self):
        issues = harness.validate_workspace(include_dashboard=False)
        self.assertEqual(issues, [], f"unexpected harness issues: {issues}")


if __name__ == "__main__":
    unittest.main()
