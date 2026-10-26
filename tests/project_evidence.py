"""Tests that replay saved official snapshots kept in the app project.

The saved bank pages and the Dart fixture live in the app project
(audit/ and test/fixtures/), not in the published feed repository, whose
README forbids uploading announcement texts. In the standalone feed
repository these tests are skipped explicitly; the live fingerprint check
in `pipeline.py --publish` still guards every publication there.
"""
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HAS_PROJECT_EVIDENCE = all((PROJECT_ROOT / p).exists() for p in (
    'reward_feed/sources.json',
    'audit/c02_cube_overseas_20261024/cube.model.json',
    'audit/c02_cube_point_terms_20261024/conditions.model.json',
    'audit/reward_schema_v2_20261024/fubon-costco.htm',
    'test/fixtures/rules_v2_programs_block.json',
    'audit/v2-s6_20261025/ipass-rendered.txt',
    'audit/v2-s7_20261026/jko-payment-rendered.txt',
    'audit/v2-s7_20261026/jko-announce-rendered.txt',
))

requires_project_evidence = unittest.skipUnless(
    HAS_PROJECT_EVIDENCE,
    'needs saved official snapshots in the app project (not published with the feed repository)')
