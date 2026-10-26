"""V2-S7: unknown rounding, upTo rates, quota-notice watching and the JKO platform.

Catalogs built here are TEST DATA ONLY; the Real* classes check the published
programs against saved Chrome-rendered official text.
"""
import copy
import hashlib
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pipeline import build_feed, collect, section_text
from rules_v2 import build_programs, check_sources, load_catalog, validate_catalog
from test_rules_v2_platform import BODY, END, PAGE, START, evidence, platform_catalog

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
FEED = Path(__file__).resolve().parents[1]


def quota_catalog(body=BODY):
    """TEST DATA ONLY: the platform test program with a limited-quota rule, a plain rule and a watch."""
    catalog = platform_catalog()
    program = catalog['programs'][0]
    rule = program['rules'][0]
    rule['requires'] = [{'id': 'w.quota', 'label': '活動尚未額滿', 'quota': 'limited', 'source': 'userDeclared'}]
    plain = copy.deepcopy(rule)
    plain.update(id='w.plain', group='w.plain')
    plain.pop('requires')
    program['rules'].append(plain)
    item = program['evidence'][0]
    section = section_text(body, START, END)
    item['approvedSha256'] = hashlib.sha256(section.encode()).hexdigest()
    item['quotaWatch'] = {'phrases': ['活動額滿', '(額滿)']}
    return catalog


class SchemaTests(unittest.TestCase):
    def test_unknown_rounding_and_up_to_rates_are_valid(self):
        catalog = platform_catalog()
        rule = catalog['programs'][0]['rules'][0]
        rule['rounding'] = {'mode': 'unknown', 'basis': 'perTransaction'}
        rule['rate'] = {'type': 'upTo', 'percent': '1'}
        validate_catalog(catalog)

    def test_bad_up_to_rates_are_rejected(self):
        for name, rate in {
            'above 100': {'type': 'upTo', 'percent': '101'},
            'zero': {'type': 'upTo', 'percent': '0'},
            'extra key': {'type': 'upTo', 'percent': '1', 'selector': 'x'},
            'missing percent': {'type': 'upTo'},
        }.items():
            with self.subTest(name):
                catalog = platform_catalog()
                catalog['programs'][0]['rules'][0]['rate'] = rate
                with self.assertRaises(ValueError):
                    validate_catalog(catalog)

    def test_quota_watch_is_valid_only_on_section_evidence_with_phrases(self):
        validate_catalog(quota_catalog())
        for name, mutate in {
            'no section': lambda e: e.pop('section'),
            'empty list': lambda e: e.__setitem__('quotaWatch', {'phrases': []}),
            'duplicate': lambda e: e.__setitem__('quotaWatch', {'phrases': ['額滿', '額滿']}),
            'blank': lambda e: e.__setitem__('quotaWatch', {'phrases': ['  ']}),
            'not text': lambda e: e.__setitem__('quotaWatch', {'phrases': [3]}),
            'unknown key': lambda e: e.__setitem__('quotaWatch', {'phrases': ['額滿'], 'fuzzy': True}),
            'too long': lambda e: e.__setitem__('quotaWatch', {'phrases': ['額' * 41]}),
        }.items():
            with self.subTest(name):
                catalog = quota_catalog()
                mutate(catalog['programs'][0]['evidence'][0])
                with self.assertRaises(ValueError):
                    validate_catalog(catalog)


class QuotaWatchTests(unittest.TestCase):
    def test_no_notice_publishes_everything(self):
        published, pending = build_programs(quota_catalog(), evidence(), NOW)
        self.assertEqual(pending, {})
        self.assertEqual({r['id'] for r in published[0]['rules']}, {'w.scan', 'w.plain'})

    def test_notice_outside_the_section_withdraws_only_limited_quota_rules(self):
        for phrase in ('活動額滿', '(額滿)'):
            with self.subTest(phrase):
                page = BODY.replace('頁首導覽', f'頁首導覽 {phrase}')
                published, pending = build_programs(quota_catalog(), evidence(page), NOW)
                self.assertEqual(set(pending), {'wallet_platform/w.scan'})
                self.assertIn(phrase, pending['wallet_platform/w.scan'])
                self.assertIn('額滿', pending['wallet_platform/w.scan'])
                rules = {p['id']: {r['id'] for r in p['rules']} for p in published}
                self.assertEqual(rules['wallet_platform'], {'w.plain'})
                self.assertEqual(rules['other_platform'], {'o.base'})

    def test_phrase_inside_an_approved_section_does_not_withdraw(self):
        body = BODY.replace('每人每月回饋上限 200 點', '每人每月回饋上限 200 點 活動額滿即提前結束')
        catalog = quota_catalog(body)
        published, pending = build_programs(catalog, evidence(body), NOW)
        self.assertEqual(pending, {})

    def test_other_approved_sections_of_the_same_source_are_not_searched(self):
        body = BODY.replace('頁尾', '頁尾 第二段開始 活動額滿將公告 第二段結束')
        catalog = quota_catalog(body)
        program = catalog['programs'][0]
        second = copy.deepcopy(program['evidence'][0])
        second.pop('quotaWatch')
        second.update(id='w.second', section={'start': '第二段開始', 'end': '第二段結束'},
                      anchors={'note': {'pointer': '/text', 'contains': '活動額滿將公告'}})
        second['approvedSha256'] = hashlib.sha256(section_text(body, '第二段開始', '第二段結束').encode()).hexdigest()
        program['evidence'].append(second)
        program['rules'][0]['evidenceRefs'].append('w.second#note')
        published, pending = build_programs(catalog, evidence(body), NOW)
        self.assertEqual(pending, {})

    def test_removed_sections_never_join_their_neighbours_into_a_phrase(self):
        # Without a separator, 「…活動」+「額滿…」 around the removed section would read 「活動額滿」.
        body = BODY.replace('頁首導覽 活動內容', '頁首導覽 活動活動內容').replace('注意事項結束 頁尾', '注意事項結束額滿 頁尾')
        self.assertIn('活動額滿', body.replace(section_text(body, START, END), ''))
        published, pending = build_programs(quota_catalog(body), evidence(body), NOW)
        self.assertEqual(pending, {})

    def test_broken_sibling_markers_make_the_watch_unverifiable(self):
        body = BODY + ' 第二段開始 說明'
        catalog = quota_catalog(body)
        program = catalog['programs'][0]
        second = copy.deepcopy(program['evidence'][0])
        second.pop('quotaWatch')
        second.update(id='w.second', section={'start': '第二段開始', 'end': '第二段結束'},
                      anchors={'note': {'pointer': '/text', 'contains': '說明'}})
        program['evidence'].append(second)
        published, pending = build_programs(catalog, evidence(body), NOW)
        self.assertIn('quota watch unverifiable', pending['wallet_platform/w.scan'])
        self.assertNotIn('sold-out notice', pending['wallet_platform/w.scan'])
        self.assertNotIn('wallet_platform/w.plain', pending)

    def test_unavailable_source_is_withdrawn_by_the_fingerprint_not_the_watch(self):
        published, pending = build_programs(quota_catalog(), evidence(error='timeout'), NOW)
        self.assertEqual(set(pending), {'wallet_platform/w.scan', 'wallet_platform/w.plain'})


from project_evidence import requires_project_evidence  # noqa: E402

JKO_PAYMENT = FEED.parent / 'audit/v2-s7_20261026/jko-payment-rendered.txt'
JKO_ANNOUNCE = FEED.parent / 'audit/v2-s7_20261026/jko-announce-rendered.txt'
APPROVED = {'jko.rate': '205ae6837caea138accbcb724144d88cd9a8fc0300051ac38322a222fa1ccf1a',
            'jko.terms': 'b228722a2e4d726b2fd9c255dbcccabb2f49ddd71a2bc31c724738564cccdc0d',
            'jko.tier': '9c5669eea53f0538cc8680ff0c2bdce3a5fac7b006bfa1bc4ca86124a1ce5912'}


class RealJkoTests(unittest.TestCase):
    def setUp(self):
        from test_rules_v2_cube import platform_registry
        self.catalog = load_catalog(FEED / 'programs')
        self.program = next(p for p in self.catalog['programs'] if p['id'] == 'jko_pay_platform')
        self.registry = platform_registry()

    def rules(self):
        return {r['id']: r for r in self.program['rules']}

    def test_sources_are_rendered_and_registered(self):
        from test_rules_v2_cube import registry
        jko = {s['cardId']: s for s in self.registry if s['cardId'].startswith('jko_')}
        self.assertEqual(jko, {
            'jko_pay_platform': {'cardId': 'jko_pay_platform', 'url': 'https://mkt.jkopay.com/event/jkopayment',
                                 'render': 'chrome'},
            'jko_pay_announce': {'cardId': 'jko_pay_announce', 'url': 'https://www.jkopay.com/application/announce',
                                 'render': 'chrome'}})
        validate_catalog(self.catalog)
        check_sources(self.catalog, registry() + self.registry)

    def test_rules_match_the_official_terms(self):
        rules = self.rules()
        self.assertEqual(set(rules), {'jkopay.account.silverPlus', 'jkopay.account.belowSilver', 'jkopay.otherFunding'})
        silver, below = rules['jkopay.account.silverPlus'], rules['jkopay.account.belowSilver']
        self.assertEqual(silver['rate'], {'type': 'fixed', 'percent': '1'})
        self.assertEqual(below['rate'], {'type': 'upTo', 'percent': '1'}, 'official: 最高1%; below silver unpublished')
        for rule in (silver, below):
            with self.subTest(rule['id']):
                self.assertEqual(rule['layer'], 'L2')
                self.assertEqual(rule['group'], 'jkopay.account')
                self.assertEqual(rule['rounding'], {'mode': 'unknown', 'basis': 'perTransaction'})
                self.assertNotIn('caps', rule, '無上限')
                self.assertNotIn('requires', rule)
                self.assertEqual(rule['combinable'], {'default': 'unknown'})
                self.assertFalse(rule['includesBase'])
                self.assertEqual(rule['unit'], 'jko_coins')
                self.assertEqual(rule['period'], {'from': '2026-01-01T00:00:00+08:00',
                                                  'to': '2026-12-31T23:59:59.999999+08:00'})
                conditions = rule['when']['all']
                self.assertIn({'route': {'platform': ['jkoPay'], 'funding': ['storedBalance']}}, conditions)
                self.assertIn({'presence': 'inPerson'}, conditions)
                self.assertIn({'spendType': 'general'}, conditions)
        self.assertIn({'selector': {'jko.tier': 'silverPlus'}}, silver['when']['all'])
        self.assertIn({'selector': {'jko.tier': 'belowSilver'}}, below['when']['all'])
        self.assertEqual(rules['jkopay.otherFunding']['status'], 'uncovered')
        selector = self.program['selectors']
        self.assertEqual([(s['id'], s['kind'], s['source'], s['values']) for s in selector],
                         [('jko.tier', 'tier', 'userDeclared', ['silverPlus', 'belowSilver'])])
        excluded = {x['id']: x for x in self.program['exclusions']}
        self.assertEqual(set(excluded), {'jko.ex.channels'})
        decision = excluded['jko.ex.channels']['when']['issuerDecision']
        for named in ('7-ELEVEN', '全家', '萊爾富', 'OK', '公益捐款', '中華郵政ｉ郵箱'):
            self.assertIn(named, decision, named)
        self.assertEqual(excluded['jko.ex.channels']['appliesTo'], ['jkopay.account.*'])
        unit = next(u for u in self.catalog['rewardUnits'] if u['id'] == 'jko_coins')
        self.assertEqual((unit['twdValue'], unit['cashEquivalent']), ('1', 'restricted'))

    def test_evidence_uses_the_approved_sections(self):
        items = {e['id']: e for e in self.program['evidence']}
        self.assertEqual({k: v['approvedSha256'] for k, v in items.items()}, APPROVED)
        self.assertEqual(items['jko.rate']['section'], {'start': '若選擇使用「 街口帳戶 」', 'end': '保險專區、街享券、餐廳訂位等。'})
        self.assertEqual(items['jko.terms']['section'], {'start': '不適用回饋通路', 'end': '無須另行通知。'})
        self.assertEqual(items['jko.tier']['section'], {'start': '2024/4/22 各項支付工具回饋調整公告',
                                                        'end': '以持續享有最高 1% 付款回饋，歡迎您多加利用。'})
        self.assertEqual(items['jko.tier']['sourceCard'], 'jko_pay_announce')
        for item in items.values():
            self.assertEqual((item['role'], item['format']), ('primary', 'html'))
            self.assertNotIn('quotaWatch', item, 'standing benefit, no quota')

    @requires_project_evidence
    def test_approved_sections_and_anchors_match_the_saved_render(self):
        texts = {'jko_pay_platform': JKO_PAYMENT.read_text(), 'jko_pay_announce': JKO_ANNOUNCE.read_text()}
        for item in self.program['evidence']:
            with self.subTest(item['id']):
                section = section_text(texts[item['sourceCard']], item['section']['start'], item['section']['end'])
                self.assertEqual(hashlib.sha256(section.encode()).hexdigest(), item['approvedSha256'])
                for name, anchor in item['anchors'].items():
                    self.assertIn(anchor['contains'], section, name)
        self.assertNotIn('Previous Next', ''.join(
            section_text(texts['jko_pay_platform'], e['section']['start'], e['section']['end'])
            for e in self.program['evidence'] if e['sourceCard'] == 'jko_pay_platform'), 'carousel stays outside')

    @requires_project_evidence
    def test_publishes_from_the_saved_render_and_withdraws_only_jko_on_change(self):
        from test_rules_v2_cube import reviewed_evidence, reviewed_platform_evidence
        ok = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                        platform_evidence=reviewed_platform_evidence())
        self.assertEqual(ok['programPending'], {})
        jko = next(p for p in ok['programs'] if p['id'] == 'jko_pay_platform')
        self.assertEqual({r['id'] for r in jko['rules']}, set(self.rules()))
        others = {p['id']: p for p in ok['programs'] if p['id'] != 'jko_pay_platform'}
        payment, announce = JKO_PAYMENT.read_text(), JKO_ANNOUNCE.read_text()
        for name, (page, notice) in {
            'rate changed': (payment.replace('最高1%街口幣', '最高0.5%街口幣'), announce),
            'exclusions changed': (payment.replace('「公益捐款」、', ''), announce),
            'tier changed': (payment, announce.replace('1% 街口幣回饋（銀牌以上會員）', '1% 街口幣回饋（金牌以上會員）')),
            'carousel only': (payment.replace('Previous Next', 'Next Previous'), announce),
        }.items():
            with self.subTest(name):
                pages = {'https://mkt.jkopay.com/event/jkopayment': page,
                         'https://www.jkopay.com/application/announce': notice}
                base = reviewed_platform_evidence()
                rendered = collect([s for s in self.registry if s['cardId'].startswith('jko_')], downloader=str,
                                   proof_downloader=dict, renderer=pages.__getitem__)
                feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                                  platform_evidence={**base, **rendered})
                withdrawn = {k for k in feed['programPending'] if k.startswith('jko_pay_platform/')}
                expected = set() if name == 'carousel only' else {
                    'jko_pay_platform/jkopay.account.silverPlus', 'jko_pay_platform/jkopay.account.belowSilver'}
                self.assertEqual(withdrawn, expected, feed['programPending'])
                self.assertEqual(set(feed['programPending']) - withdrawn, set())
                self.assertEqual({p['id']: p for p in feed['programs'] if p['id'] != 'jko_pay_platform'}, others)
        for failing in ('https://mkt.jkopay.com/event/jkopayment', 'https://www.jkopay.com/application/announce'):
            with self.subTest(render_failure=failing):
                pages = {'https://mkt.jkopay.com/event/jkopayment': payment,
                         'https://www.jkopay.com/application/announce': announce}

                def render(url, failing=failing, pages=pages):
                    if url == failing:
                        raise ValueError('Official source render timed out')
                    return pages[url]
                rendered = collect([s for s in self.registry if s['cardId'].startswith('jko_')], downloader=str,
                                   proof_downloader=dict, renderer=render)
                feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                                  platform_evidence={**reviewed_platform_evidence(), **rendered})
                self.assertEqual(set(feed['programPending']), {'jko_pay_platform/jkopay.account.silverPlus',
                                                               'jko_pay_platform/jkopay.account.belowSilver'})
                self.assertEqual({p['id']: p for p in feed['programs'] if p['id'] != 'jko_pay_platform'}, others)


class RealIpassQuotaWatchTests(unittest.TestCase):
    PHRASES = ['活動額滿', '活動已額滿', '（額滿）', '(額滿)']

    def setUp(self):
        self.catalog = load_catalog(FEED / 'programs')
        self.program = next(p for p in self.catalog['programs'] if p['id'] == 'ipass_money_platform')

    def test_header_watches_the_approved_phrases(self):
        watched = {e['id']: e.get('quotaWatch') for e in self.program['evidence']}
        self.assertEqual(watched, {'ipass.header': {'phrases': self.PHRASES}, 'ipass.terms': None})
        quota = [q for r in self.program['rules'] for q in r.get('requires', []) if q.get('quota') == 'limited']
        self.assertEqual([q['id'] for q in quota], ['ipass.campaign.quotaOpen'])

    @requires_project_evidence
    def test_saved_render_has_no_notice_and_a_banner_withdraws_only_ipass(self):
        from test_rules_v2_cube import IPASS_SNAPSHOT, platform_registry, reviewed_evidence, reviewed_platform_evidence
        text = IPASS_SNAPSHOT.read_text()
        outside = text
        for item in self.program['evidence']:
            outside = outside.replace(section_text(text, item['section']['start'], item['section']['end']), '')
        self.assertEqual({p: outside.count(p) for p in self.PHRASES}, dict.fromkeys(self.PHRASES, 0))
        self.assertEqual(outside.count('已額滿'), 1, 'why 已額滿 alone was not approved')
        ok = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                        platform_evidence=reviewed_platform_evidence())
        self.assertEqual(ok['programPending'], {})
        ipass = [s for s in platform_registry() if s['cardId'] == 'ipass_money_platform']
        banner = text.replace('消費享10%回饋！', '（額滿） 消費享10%回饋！', 1)
        rendered = collect(ipass, downloader=str, proof_downloader=dict, renderer=lambda u: banner)
        feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                          platform_evidence={**reviewed_platform_evidence(), **rendered})
        self.assertEqual(set(feed['programPending']), {'ipass_money_platform/ipass.twqr.scan'})
        self.assertIn('（額滿）', feed['programPending']['ipass_money_platform/ipass.twqr.scan'])


if __name__ == '__main__':
    unittest.main()
