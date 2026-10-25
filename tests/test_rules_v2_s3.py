"""Reviewed CUBE S3 data contract; uses saved official documents, never network."""
import json
import unittest
from test_rules_v2_cube import FEED, ROOT, raw_documents, registry, reviewed_evidence, NOW, platform_registry, reviewed_platform_evidence
from rules_v2 import load_catalog, validate_catalog, check_sources, resolve_pointer
from pipeline import build_feed
from project_evidence import requires_project_evidence


class CubeS3Tests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(FEED / 'programs')
        self.program = self.catalog['programs'][0]
        self.rules = {r['id']: r for r in self.program['rules']}

    def test_general_insurance_installment_and_overflow(self):
        for rid in ['cube.general', 'cube.insurance', 'cube.installment']:
            self.assertEqual(self.rules[rid]['rate'], {'type': 'fixed', 'percent': '0.3'})
        for rid in ['cube.fixed.domestic', 'cube.fixed.overseasPhysical']:
            self.assertEqual(self.rules[rid]['caps'][0]['overflowRule'], 'cube.general')

    @requires_project_evidence
    def test_three_level_rates_and_routes_match_official_text(self):
        # S1 exact Level statements, not the old advertising highest rate.
        raw = json.loads(next(iter(raw_documents().values())).read_text())
        notice = raw[':items']['root'][':items']['responsivegrid'][':items']['cub_main'][':items']['cub_notice_copy']['text']
        for plan in ['digital', 'dining_shopping', 'travel']:
            rules = [r for r in self.program['rules'] if r['id'].startswith('cube.'+plan+'.') and r.get('status') != 'uncovered']
            self.assertTrue(rules, plan)
            for r in rules:
                self.assertEqual(r['rate'], {'type': 'bySelector', 'selector': 'cube_level', 'table': {'L1':'2','L2':'3','L3':'3.3'}})
                self.assertIn({'route': {'platform': ['direct','applePay','samsungPay','googlePay','garminPay'], 'funding':['creditCard']}}, r['when']['all'])
                self.assertEqual(r['period'], {'from':'2026-01-01T00:00:00+08:00','to':'2026-12-31T23:59:59.999999+08:00'})
                self.assertTrue(r['includesBase'])
        self.assertIn('指定消費',notice)
        all_raw = json.dumps(raw, ensure_ascii=False)
        for text in ['指定消費2%', '指定消費3%', '指定消費3.3%']:
            self.assertIn(text,all_raw)

    def test_pxpay_is_distinct_from_pxplus_and_period_is_reviewed(self):
        r = self.rules['cube.pxpay.partners']
        self.assertEqual(r['period']['from'],'2026-04-22T00:00:00+08:00')
        self.assertEqual(r['rate']['percent'],'2')
        self.assertIn({'route': {'platform':['pxPlus'], 'funding':['creditCard']}},r['when']['all'])
        self.assertIn('pxPay',json.dumps(self.program))

    @requires_project_evidence
    def test_catalog_anchors_and_sources_and_offline_feed(self):
        validate_catalog(self.catalog)
        check_sources(self.catalog,registry()+platform_registry())
        for e in self.program['evidence']:
            raw = json.loads(raw_documents()[e['url']].read_text())
            for name,a in e['anchors'].items():
                self.assertIn(a['contains'],resolve_pointer(raw,a['pointer']), (e['id'],name))
        feed = build_feed([],reviewed_evidence(),['cathay_cube', 'fubon_costco'],NOW,programs=self.catalog,platform_evidence=reviewed_platform_evidence())
        self.assertEqual(feed['programPending'],{})
        legacy = build_feed([],reviewed_evidence(),['cathay_cube', 'fubon_costco'],NOW)
        self.assertEqual(feed['assessmentProfiles'], legacy['assessmentProfiles'])

    def test_complex_categories_remain_uncovered_with_period_and_reason(self):
        for plan in ['autumn_japan','birthday','kids','formosa']:
            rows = [r for r in self.program['rules'] if r['id'].startswith('cube.'+plan+'.')]
            self.assertTrue(rows, plan)
            self.assertTrue(all(r['status']=='uncovered' and '2026/' in r['notes'] for r in rows))


class CubeS3OfficialMappingTests(unittest.TestCase):
    @requires_project_evidence
    def test_each_priced_category_keeps_official_items_and_explicit_recognition(self):
        catalog=load_catalog(FEED / 'programs')
        p=catalog['programs'][0]
        sets={s['id']:s for s in catalog['merchantSets']}
        s2=next(e for e in p['evidence'] if e['id']=='cube.s2')
        doc=json.loads(raw_documents()[s2['url']].read_text())
        import re
        from html import unescape
        def plain(s): return unescape(re.sub('<[^>]+>',' ',s)).strip()
        def items(node):
            if isinstance(node,dict):
                if 'itemText' in node: yield plain(node['itemText'])
                for value in node.values(): yield from items(value)
            elif isinstance(node,list):
                for value in node: yield from items(value)
        for r in p['rules']:
            if r.get('status')=='uncovered' or r['id'].startswith('cube.fixed') or r['kind']=='base':continue
            with self.subTest(rule=r['id']):
                category_ref=next(ref for ref in r['evidenceRefs'] if ref.startswith('cube.s2#') and not ref.endswith(('_title','_notice')))
                anchor=s2['anchors'][category_ref.split('#')[1]]
                category=resolve_pointer(doc,anchor['pointer'].rsplit('/',1)[0])
                set_id='cube_'+r['id'][5:].replace('.','_')
                if set_id=='cube_pxpay_partners':continue # partner definition, not closed bank brand enumeration
                if set_id not in ['cube_dining_shopping_restaurants']:
                    self.assertTrue(set(items(category)) <= set(sets[set_id]['match']['brands']))
                self.assertTrue(any('issuerDecision' in c for c in r['when']['all']))
                self.assertIn({'spendType':'general'},r['when']['all'])
                self.assertEqual(r['caps'][0]['amount'],'creditLimit+500000')
                self.assertEqual(r['caps'][0]['overflowRule'],'cube.general')
                title_ref=next(ref for ref in r['evidenceRefs'] if ref.endswith('_title'))
                title=plain(resolve_pointer(doc,s2['anchors'][title_ref.split('#')[1]]['pointer']))
                dates=re.search(r'2026/(\d+)/(\d+)~2026/(\d+)/(\d+)',title)
                self.assertIsNotNone(dates)
                m,d,em,ed=map(int,dates.groups())
                self.assertTrue(r['period']['from'].startswith(f'2026-{m:02}-{d:02}T00:00:00'))
                self.assertTrue(r['period']['to'].startswith(f'2026-{em:02}-{ed:02}T23:59:59'))
                if r['rate']['type']=='fixed':
                    self.assertEqual(r['rate']['percent'],'2')
                    self.assertIn('2%',title)

class CubeS3ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        catalog = load_catalog(FEED / 'programs')
        self.program = catalog['programs'][0]
        self.sets = {m['id']: m for m in catalog['merchantSets']}
        self.exclusions = {x['id']: x for x in self.program['exclusions']}

    def test_official_names_are_usable_candidates_with_original_restrictions_retained(self):
        aliases = {
            'cube_digital_shopping': ['PChome 24h購物', '小樹購', 'momo-EC'],
            'cube_digital_international': ['淘寶', '天貓', 'Coupang 酷澎'],
            'cube_digital_streaming': ['App Store', 'Apple Music', 'iCloud', 'Apple TV+', 'Apple Arcade', 'Apple One', 'iTunes'],
            'cube_select_parking': ['車麻吉', 'uTagGo'],
            'cube_select_convenience': ['7-11'],
            'cube_dining_shopping_department': ['MITSUI OUTLET PARK'],
        }
        for sid, names in aliases.items():
            with self.subTest(set=sid):
                self.assertTrue(set(names) <= set(self.sets[sid]['match']['brands']))
        self.assertIn('車麻吉(不含加油、充電)', self.sets['cube_select_parking']['match']['brands'])
        self.assertIn('PChome 24h購物(不含儲值及電子票券)', self.sets['cube_digital_shopping']['match']['brands'])

    def test_residual_exclusion_applies_to_both_fixed_rules(self):
        self.assertTrue({'cube.fixed.domestic', 'cube.fixed.overseasPhysical'} <=
                        set(self.exclusions['cube.ex.otherGeneral']['appliesTo']))
        # Do not suppress designated exceptions such as select parking/retail.
        self.assertFalse(any('select' in x for x in self.exclusions['cube.ex.otherGeneral']['appliesTo']))

    @requires_project_evidence
    def test_fixed_overseas_retains_point_terms_general_exclusions(self):
        for xid in ['cube.ex.convenience', 'cube.ex.pxmart', 'cube.ex.micropay', 'cube.ex.non_purchase']:
            with self.subTest(exclusion=xid):
                self.assertIn('cube.fixed.overseasPhysical', self.exclusions[xid]['appliesTo'])
        # The source really retains point terms in addition to overseas-specific exclusions.
        s1 = next(e for e in self.program['evidence'] if e['id'] == 'cube.s1')
        raw = json.loads(raw_documents()[s1['url']].read_text())
        notice = resolve_pointer(raw, s1['anchors']['overseas_definition']['pointer'])
        self.assertIn('其他依小樹點（信用卡）累積辦法與相關公告排除', notice)

if __name__ == '__main__': unittest.main()
