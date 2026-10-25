"""Actual official saved Costco program, never a made-up bank fixture."""
import copy
import json
import unittest

from assessment_profiles import APPROVALS, SUPPORT_APPROVALS
from pipeline import build_feed, normalize
from rules_v2 import load_catalog, validate_catalog, check_sources
from test_rules_v2_cube import FEED, ROOT, NOW, registry, reviewed_evidence
from project_evidence import requires_project_evidence


class CostcoProgramTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(FEED / 'programs')
        self.program = next(p for p in self.catalog['programs'] if p['id'] == 'fubon_costco_credit')
        self.rules = {r['id']: r for r in self.program['rules']}

    @requires_project_evidence
    def test_valid_catalog_and_both_approved_text_sources(self):
        validate_catalog(self.catalog)
        check_sources(self.catalog, registry())
        saved = ROOT / 'audit/reward_schema_v2_20261024'
        self.assertEqual({e['format'] for e in self.program['evidence']}, {'html', 'pdf'})
        for e in self.program['evidence']:
            expected = APPROVALS['fubon_costco'] if e['role'] == 'primary' else SUPPORT_APPROVALS['fubon_costco'][e['url']]
            self.assertEqual(e['approvedSha256'], expected)
            text = normalize((saved / 'fubon-costco.htm').read_text()) if e['format'] == 'html' else (saved / 'fubon-costco-terms.txt').read_text()
            for name, anchor in e['anchors'].items():
                with self.subTest(source=e['id'], anchor=name):
                    self.assertEqual(anchor['pointer'], '/text')
                    self.assertIn(anchor['contains'], text)

    def test_all_priced_rules_and_exclusions_bind_both_sources(self):
        ids = {e['id'] for e in self.program['evidence']}
        for r in self.program['rules'] + self.program['exclusions']:
            if r.get('status') != 'uncovered':
                self.assertEqual({s.split('#')[0] for s in r['evidenceRefs']}, ids, r['id'])

    def test_basic_rates_unit_rounding_and_membership(self):
        for rid, rate in [('outside','1'),('instore','2'),('online','3')]:
            r=self.rules['costco.'+rid]
            self.assertEqual(r['rate'], {'type':'fixed','percent':rate})
            self.assertEqual(r['rounding'], {'mode':'halfUp','basis':'perTransaction'})
            self.assertEqual(r['unit'], 'costco_gold')
            self.assertIn({'selector':{'costco_member':True}}, r['when']['all'])
        units={u['id']:u for u in self.catalog['rewardUnits']}
        self.assertEqual(units['costco_gold']['cashEquivalent'],'restricted')
        self.assertEqual(units['costco_gold']['twdValue'],'1')
        self.assertEqual(units['costco_gold']['expiry'],{'rule':'annualReset','resetOn':'03-31','accrualWindow':'prevYear02-01..thisYear01-31'})
        self.assertEqual(units['fubon_card_credit']['cashEquivalent'],'yes')
        self.assertIn('MyCharge', units['costco_gold']['redemptionNote'])

    def test_convenience_exclusion_is_platform_independent_and_full_px_group(self):
        exclusions={r['id']:r for r in self.program['exclusions']}
        self.assertEqual(exclusions['costco.ex.convenience']['when'], {'merchantIn':'fubon_convenience'})
        self.assertEqual(exclusions['costco.ex.pxgroup']['when'], {'merchantIn':'fubon_pxgroup'})
        self.assertEqual(exclusions['costco.ex.types']['when'], {'spendType':['insurance','installment','storedValue','topup','cashAdvance','tuition','government','tax','medical','utility','fee']})
        self.assertIn('issuerDecision', exclusions['costco.ex.other']['when'])

    def test_ubereats_bonus_is_separate_and_monthly(self):
        r=self.rules['costco.ubereats.bonus']
        self.assertEqual(r['rate'],{'type':'fixed','percent':'1'})
        self.assertFalse(r['includesBase'])
        self.assertEqual(r['rounding'],{'mode':'halfUp','basis':'monthlyAggregate','scope':'primaryAndSupplementary'})
        self.assertEqual(r['period']['from'],'2026-07-01T00:00:00+08:00')
        self.assertIn({'route':{'platform':['direct'],'funding':['creditCard']}},r['when']['all'])
        self.assertNotIn('caps',r)

    def test_rail_table_is_bonus_only_and_caps_are_conditional(self):
        r=self.rules['costco.rail.bonus']
        self.assertEqual(r['rate']['table'], {
            'world|6000|false':'2','world|12000|false':'3',
            'world|6000|true':'5','world|12000|true':'7',
            'titanium_business|6000|false':'1','titanium_business|12000|false':'2',
            'titanium_business|6000|true':'3','titanium_business|12000|true':'5',
            'titanium|6000|false':'1','titanium|12000|false':'2',
            'titanium|6000|true':'3','titanium|12000|true':'5'})
        self.assertFalse(r['includesBase'])
        self.assertEqual(r['unit'],'fubon_card_credit')
        self.assertEqual(r['rounding'],{'mode':'halfUp','basis':'monthlyAggregate','scope':'household'})
        self.assertEqual([(c['amount'],c['when']) for c in r['caps']], [('550',{'selector':{'costco_variant':'world'}}),('450',{'selector':{'costco_variant':['titanium_business','titanium']}})])
        self.assertTrue(all(c['overflowRule'] is None and c['per']=='calendarMonth' for c in r['caps']))
        self.assertEqual(r['requires'][0]['deadline'],'monthLastDay')
        self.assertEqual(r['requires'][0]['per'],'calendarMonth')

    def test_special_campaigns_remain_uncovered(self):
        for rid in ['insurance','overseas','mastercard','newCard','installmentSpecial','gasBonus']:
            self.assertEqual(self.rules['costco.uncovered.'+rid]['status'],'uncovered')

    def test_payment_names_and_partial_chinese_words_are_not_merchant_identity(self):
        sets={s['id']:s for s in self.catalog['merchantSets']}
        with self.subTest('payment tool is not a PX group merchant'):
            self.assertFalse({'全支付','PXPay','Px pay'} & set(sets['fubon_pxgroup']['match']['brands']))
        with self.subTest('partial convenience name is ambiguous'):
            self.assertNotIn('全家',sets['fubon_convenience']['match']['brands'])
        self.assertTrue(sets['fubon_pxgroup']['openEnded'])
        self.assertTrue(sets['fubon_convenience']['openEnded'])

    @requires_project_evidence
    def test_current_fixture_equals_actual_backend_and_v1_profiles_unchanged(self):
        evidence=reviewed_evidence()
        feed=build_feed([],evidence,['cathay_cube','fubon_costco'],NOW,programs=self.catalog)
        self.assertEqual(feed['programPending'],{})
        legacy=build_feed([],copy.deepcopy(evidence),['cathay_cube','fubon_costco'],NOW)
        self.assertEqual({k:v for k,v in feed.items() if k in legacy},legacy)
        fixture=json.loads((ROOT/'test/fixtures/rules_v2_programs_block.json').read_text())
        self.assertEqual(fixture,{k:feed[k] for k in fixture})

    def test_either_source_missing_changed_failed_or_stale_withdraws_all_costco_only(self):
        valid=reviewed_evidence()
        baseline=build_feed([],valid,['cathay_cube','fubon_costco'],NOW,programs=self.catalog)
        cube=next(p for p in baseline['programs'] if p['id']=='cathay_cube_credit')
        priced={r['id'] for r in self.program['rules'] if r.get('status')!='uncovered'}
        pdf=next(e['url'] for e in self.program['evidence'] if e['format']=='pdf')
        for source in ['primary','pdf']:
            for failure in ['missing','changed','failed','stale']:
                with self.subTest(source=source,failure=failure):
                    evidence=copy.deepcopy(valid)
                    parent=evidence if source=='primary' else evidence['fubon_costco']['supportingSources']
                    key='fubon_costco' if source=='primary' else pdf
                    if failure=='missing': parent.pop(key)
                    elif failure=='changed': parent[key]['sha256']='0'*64
                    elif failure=='failed': parent[key]={'error':'timeout'}
                    else: parent[key]['error']='stale cached source'
                    feed=build_feed([],evidence,['cathay_cube','fubon_costco'],NOW,programs=self.catalog)
                    p=next(p for p in feed['programs'] if p['id']=='fubon_costco_credit')
                    self.assertFalse(priced & {r['id'] for r in p['rules']})
                    self.assertEqual({f'fubon_costco_credit/{rid}' for rid in priced}, {k for k in feed['programPending'] if k.startswith('fubon_costco_credit/')})
                    self.assertEqual(next(p for p in feed['programs'] if p['id']=='cathay_cube_credit'),cube)
