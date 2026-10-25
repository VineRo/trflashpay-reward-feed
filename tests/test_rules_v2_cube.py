"""CUBE fixed plan expressed in schema v2 stays equivalent to the reviewed v1 formula."""
import copy
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from assessment_profiles import APPROVALS, SUPPORT_APPROVALS
from pipeline import CUBE_POINT_TERMS_URL, CUBE_SCHEME_URL, build_feed, collect, normalize, normalize_aem_model, supporting_proof
from rules_v2 import check_sources, load_catalog, resolve_pointer, validate_catalog
from project_evidence import requires_project_evidence

ROOT = Path(__file__).resolve().parents[2]
# The feed's own files, valid in the app project and in the standalone feed repository.
FEED = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
FIXED_RULES = {'cube.fixed.domestic', 'cube.fixed.overseasPhysical'}


def registry():
    return json.loads((FEED / 'sources.json').read_text())


def raw_documents():
    """Saved official raw models whose normalized digests match current approvals."""
    cube = [s for s in registry() if s['cardId'] == 'cathay_cube'][0]
    return {
        cube['url']: ROOT / 'audit/c02_cube_overseas_20261024/cube.model.json',
        CUBE_SCHEME_URL: ROOT / 'audit/c02_cube_overseas_20261024/cube-list.model.json',
        CUBE_POINT_TERMS_URL: ROOT / 'audit/c02_cube_point_terms_20261024/conditions.model.json',
    }


def reviewed_evidence():
    """All saved approved programs for the shared actual-backend fixture."""
    sources = [s for s in registry() if s['cardId'] in ('cathay_cube', 'fubon_costco')]
    docs = raw_documents()
    costco = next(s for s in sources if s['cardId'] == 'fubon_costco')
    docs[costco['url']] = ROOT / 'audit/reward_schema_v2_20261024/fubon-costco.htm'
    docs[costco['supportingUrls'][0]] = ROOT / 'audit/reward_schema_v2_20261024/fubon-costco-terms.pdf'
    return collect(sources, downloader=lambda url: (normalize(docs[url].read_text()) if url == costco['url']
                                                    else normalize_aem_model(docs[url].read_bytes())),
                   proof_downloader=lambda url: supporting_proof(url, docs[url].read_bytes()))


def platform_registry():
    return json.loads((FEED / 'platform_sources.json').read_text())


IPASS_SNAPSHOT = ROOT / 'audit/v2-s6_20261025/ipass-rendered.txt'


def reviewed_platform_evidence():
    """Platform sources from the saved Chrome-rendered official text (approved 2026-10-25)."""
    snapshots = {s['url']: IPASS_SNAPSHOT for s in platform_registry() if s['cardId'] == 'ipass_money_platform'}
    return collect(platform_registry(), downloader=lambda url: None, proof_downloader=lambda url: {},
                   renderer=lambda url: snapshots[url].read_text())


def cube_program(catalog):
    return next(p for p in catalog['programs'] if p['id'] == 'cathay_cube_credit')


class CubeProgramDataTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(FEED / 'programs')
        self.program = cube_program(self.catalog)

    def test_program_is_valid_and_registered(self):
        validate_catalog(self.catalog)
        check_sources(self.catalog, registry() + platform_registry())

    def test_fixed_plan_rules_are_present(self):
        self.assertTrue(FIXED_RULES <= {r['id'] for r in self.program['rules']})

    def test_approved_digests_match_existing_reviewed_approvals(self):
        for item in self.program['evidence']:
            with self.subTest(item['id']):
                expected = (APPROVALS[item['sourceCard']] if item['role'] == 'primary'
                            else SUPPORT_APPROVALS[item['sourceCard']][item['url']])
                self.assertEqual(item['approvedSha256'], expected)

    @requires_project_evidence
    def test_every_anchor_resolves_to_official_text(self):
        docs = raw_documents()
        for item in self.program['evidence']:
            data = json.loads(docs[item['url']].read_text())
            for name, anchor in item['anchors'].items():
                with self.subTest(evidence=item['id'], anchor=name):
                    value = resolve_pointer(data, anchor['pointer'])
                    self.assertIsInstance(value, str)
                    self.assertIn(anchor['contains'], value)

    def test_both_fixed_rules_require_general_spend_and_overseas_excludes_onboard_and_medical(self):
        # Official fixed-plan text: overseas excludes medical, gambling, purchases on
        # planes/cruises and non-purchase items; v1 requires eligibleGeneral too.
        rules = {r['id']: r for r in self.program['rules']}
        for rid in FIXED_RULES:
            with self.subTest(rid):
                self.assertIn({'spendType': 'general'}, rules[rid]['when']['all'])
        overseas = [x for x in self.program['exclusions'] if 'cube.fixed.overseasPhysical' in x['appliesTo']]
        excluded = set()
        for item in overseas:
            for leaf in item['when'].get('any', [item['when']]):
                values = leaf.get('spendType', [])
                excluded |= set(values if isinstance(values, list) else [values])
        self.assertTrue({'onboard', 'medical'} <= excluded)

    def test_every_rule_and_exclusion_cites_all_three_reviewed_cube_sources(self):
        # v1 withdraws the fixed formula when any of S1/S2/point terms changes;
        # v2 keeps that dependency so the two cannot diverge.
        ids = {e['id'] for e in self.program['evidence']}
        self.assertEqual(len(ids), 3)
        for item in self.program['rules'] + self.program['exclusions']:
            if item.get('status') == 'uncovered':
                continue
            with self.subTest(item['id']):
                self.assertEqual({ref.split('#')[0] for ref in item['evidenceRefs']}, ids)


class CubeEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(FEED / 'programs')
        self.evidence = reviewed_evidence()

    def feed(self, evidence):
        return build_feed([], copy.deepcopy(evidence), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                          platform_evidence=reviewed_platform_evidence())

    def published(self, feed):
        program = cube_program(feed)
        return {r['id']: r for r in program['rules']}

    @requires_project_evidence
    def test_feed_keeps_schema_one_and_adds_v2_block(self):
        feed = self.feed(self.evidence)
        self.assertEqual(feed['schemaVersion'], 1)
        self.assertEqual(feed['programsSchemaVersion'], 2)
        self.assertEqual(feed['programPending'], {})
        legacy = build_feed([], copy.deepcopy(self.evidence), ['cathay_cube', 'fubon_costco'], NOW)
        self.assertNotIn('programs', legacy)
        self.assertEqual(set(feed) - set(legacy),
                         {'programsSchemaVersion', 'rewardUnits', 'merchantSets', 'programs', 'programPending'})

    @requires_project_evidence
    def test_rates_periods_unit_and_rounding_match_v1(self):
        feed = self.feed(self.evidence)
        calc = feed['assessmentProfiles']['cathay_cube']['calculation']
        rules = self.published(feed)
        domestic, overseas = rules['cube.fixed.domestic'], rules['cube.fixed.overseasPhysical']
        self.assertEqual(Decimal(domestic['rate']['percent']), Decimal(str(calc['rate'])))
        self.assertEqual(Decimal(overseas['rate']['percent']), Decimal(str(calc['overseasPhysicalRate'])))
        units = {u['id']: u for u in feed['rewardUnits']}
        for item in (domestic, overseas):
            with self.subTest(item['id']):
                self.assertEqual(item['period']['from'], calc['validFrom'])
                self.assertEqual(datetime.fromisoformat(item['period']['to']),
                                 datetime.fromisoformat(calc['validUntil']))
                self.assertEqual(units[item['unit']]['label'], calc['unit'])
                self.assertEqual(item['rounding'], {'mode': calc['rounding'], 'basis': 'perTransaction'})
                self.assertTrue(item['includesBase'])

    def test_every_v1_withdrawal_also_withdraws_v2_fixed_rules(self):
        scenarios = {}
        for url in [CUBE_SCHEME_URL, CUBE_POINT_TERMS_URL]:
            for label, value in {'missing': None, 'changed': {'sha256': '0' * 64}, 'failed': {'error': 'timeout'},
                                 'failed with old digest': 'stale'}.items():
                evidence = copy.deepcopy(self.evidence)
                supports = evidence['cathay_cube']['supportingSources']
                if value is None:
                    supports.pop(url)
                elif value == 'stale':
                    supports[url] = {**supports[url], 'error': 'stale digest'}
                else:
                    supports[url] = {'url': url, **value}
                scenarios[f'{url.rsplit("/", 1)[-1]} {label}'] = evidence
        primary_changed = copy.deepcopy(self.evidence)
        primary_changed['cathay_cube']['sha256'] = '0' * 64
        scenarios['primary changed'] = primary_changed
        scenarios['primary unavailable'] = {'cathay_cube': {'url': self.evidence['cathay_cube']['url'],
                                                            'error': 'down'}}
        for name, evidence in scenarios.items():
            with self.subTest(name):
                feed = self.feed(evidence)
                self.assertIsNone(feed['assessmentProfiles']['cathay_cube']['calculation'])
                self.assertFalse(FIXED_RULES & set(self.published(feed)))
                self.assertTrue({f'cathay_cube_credit/{r}' for r in FIXED_RULES} <= set(feed['programPending']))

    @requires_project_evidence
    def test_dart_compat_fixture_matches_current_backend_output(self):
        fixture = json.loads((ROOT / 'test/fixtures/rules_v2_programs_block.json').read_text())
        feed = self.feed(self.evidence)
        self.assertEqual(fixture, {key: feed[key] for key in fixture})
        self.assertEqual(set(fixture), set(feed) - set(build_feed([], copy.deepcopy(self.evidence),
                                                                    ['cathay_cube', 'fubon_costco'], NOW)))

    @requires_project_evidence
    def test_valid_evidence_publishes_both_formats(self):
        feed = self.feed(self.evidence)
        self.assertIsNotNone(feed['assessmentProfiles']['cathay_cube']['calculation'])
        self.assertTrue(FIXED_RULES <= set(self.published(feed)))


if __name__ == '__main__':
    unittest.main()
