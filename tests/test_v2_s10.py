"""V2-S10: platform coverage table, official JSON APIs, plain-download sources,
campaign discovery and three-tier classification.

Catalogs and API responses built here are TEST DATA ONLY; the Real*/Replay*
classes use the published programs and saved official listings.
"""
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pipeline
from pipeline import build_feed, collect
from rules_v2 import build_programs, canonical_json, load_catalog, validate_catalog
from project_evidence import requires_project_evidence
from test_rules_v2_platform import platform_catalog

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
FEED = Path(__file__).resolve().parents[1]
API = 'https://wallet.example/api/campaign'


def platform_map():
    return json.loads((FEED / 'platform_map.json').read_text())


class FakeTransport:
    """Records requests and answers with a fixed (status, bytes)."""

    def __init__(self, status=200, payload=None, raw=None):
        self.status, self.raw = status, raw if raw is not None else json.dumps(payload).encode()
        self.calls = []

    def __call__(self, url, method, data, headers, timeout):
        self.calls.append((url, method, data, headers, timeout))
        return self.status, self.raw


def api_source(**extra):
    return {'cardId': 'wallet_api', 'url': API, 'fetch': 'api', 'method': 'POST', 'body': {'body': {}},
            'okPointer': '/header/returnCode', 'okValue': 'OK', **extra}


OK_PAYLOAD = {'header': {'returnCode': 'OK', 'txSn': 'random-1'},
              'body': {'detail': {'content': '<p>單筆享 10% 回饋，每月上限 200 點</p>', 'end': '2026-12-31'},
                       'banner': '輪播 1'}}


# ---------------------------------------------------------------- coverage

class CoverageTests(unittest.TestCase):
    def setUp(self):
        from coverage_table import build_platform_coverage
        self.build = build_platform_coverage
        self.catalog = load_catalog(FEED / 'programs')

    def published(self, evidence=None):
        from test_rules_v2_cube import reviewed_evidence, reviewed_platform_evidence
        return build_programs(self.catalog, {**reviewed_evidence(), **(evidence if evidence is not None
                                                                         else reviewed_platform_evidence())}, NOW)

    @requires_project_evidence  # modeled cells need the saved approved renders
    def test_forty_cells_each_classified(self):
        published, pending = self.published()
        table = self.build(self.catalog, published, pending, platform_map())
        self.assertEqual(len(table['cells']), 40)
        self.assertTrue(all('asOf' in c and isinstance(c['sources'], list) for c in table['cells']))
        self.assertEqual({c['status'] for c in table['cells']} - {'modeled', 'withdrawn', 'reasoned'}, set())
        self.assertEqual(sum(table['summary'].values()), 40)
        cell = {(c['platform'], c['funding']): c for c in table['cells']}
        self.assertEqual(cell[('jkoPay', 'storedBalance')]['status'], 'modeled')
        self.assertEqual(cell[('ipassMoney', 'creditCard')]['status'], 'modeled')
        for funding in ('bankAccount', 'debitCard', 'creditCard'):
            reason = cell[('jkoPay', funding)]['reason']
            self.assertEqual(cell[('jkoPay', funding)]['status'], 'reasoned')
            self.assertIn('銀行', reason, funding)
        self.assertIn('App', cell[('linePay', 'storedBalance')]['reason'])
        self.assertIn('App', cell[('pxPlus', 'bankAccount')]['reason'])
        for c in table['cells']:
            self.assertTrue(c['programId'] and len(c.get('reason') or '') >= 10 or c['status'] == 'modeled', c)

    def test_withdrawn_rules_mark_their_cells_with_the_reason(self):
        published, pending = self.published(evidence={})
        table = self.build(self.catalog, published, pending, platform_map())
        cell = {(c['platform'], c['funding']): c for c in table['cells']}
        self.assertEqual(cell[('jkoPay', 'storedBalance')]['status'], 'withdrawn')
        self.assertIn('source', cell[('jkoPay', 'storedBalance')]['reason'])
        self.assertEqual(cell[('ipassMoney', 'storedBalance')]['status'], 'withdrawn')

    def test_a_missing_reason_or_program_fails(self):
        published, pending = self.published()
        broken = copy.deepcopy(self.catalog)
        jko = next(p for p in broken['programs'] if p['id'] == 'jko_pay_platform')
        jko['fundingNotes'].pop('debitCard')
        with self.assertRaisesRegex(ValueError, 'jkoPay.*debitCard'):
            self.build(broken, published, pending, platform_map())
        with self.assertRaisesRegex(ValueError, 'no program'):
            self.build(self.catalog, published, pending, {**platform_map(), 'linePay': 'missing_platform'})
        with self.assertRaisesRegex(ValueError, 'every payment app'):
            self.build(self.catalog, published, pending, {k: v for k, v in platform_map().items() if k != 'linePay'})

    def test_priced_programs_may_not_use_a_wildcard(self):
        published, pending = self.published()
        broken = copy.deepcopy(self.catalog)
        jko = next(p for p in broken['programs'] if p['id'] == 'jko_pay_platform')
        jko['fundingNotes'] = {'*': '依各銀行公告，街口平台層未建模的付款方式'}
        with self.assertRaisesRegex(ValueError, 'wildcard'):
            self.build(broken, published, pending, platform_map())

    def test_funding_notes_reject_vacuous_reasons(self):
        for note in ('未建模', '尚未建模。', '  ', 'N/A'):
            with self.subTest(note):
                catalog = platform_catalog()
                catalog['programs'][0]['fundingNotes'] = {'creditCard': note}
                with self.assertRaises(ValueError):
                    validate_catalog(catalog)
        catalog = platform_catalog()
        catalog['programs'][0]['fundingNotes'] = {'cash': '現金付款不是本專案的資金來源'}
        with self.assertRaises(ValueError):
            validate_catalog(catalog)

    @requires_project_evidence  # modeled cells need the saved approved renders
    def test_feed_build_fails_without_coverage(self):
        from test_rules_v2_cube import reviewed_evidence, reviewed_platform_evidence
        feed = build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                          platform_evidence=reviewed_platform_evidence(), platform_map=platform_map())
        self.assertEqual(len(feed['platformCoverage']['cells']), 40)
        with self.assertRaises(ValueError):
            build_feed([], reviewed_evidence(), ['cathay_cube', 'fubon_costco'], NOW, programs=self.catalog,
                       platform_evidence=reviewed_platform_evidence(),
                       platform_map={**platform_map(), 'linePay': 'missing_platform'})

    def test_platform_map_matches_the_app(self):
        dart = (FEED.parent / 'lib/services/scan_recommendation_service.dart')
        if not dart.exists():
            self.skipTest('app source not in the standalone feed repository')
        import re
        block = dart.read_text().split('static const platformPrograms = {', 1)[1].split('};', 1)[0]
        app = dict(re.findall(r"PaymentPlatform\.(\w+): '(\w+)'", block))
        self.assertEqual(app, platform_map())


# ---------------------------------------------------------------- JSON API

class ApiFetchTests(unittest.TestCase):
    def test_fetch_api_posts_json_and_canonicalises(self):
        transport = FakeTransport(payload=OK_PAYLOAD)
        doc = pipeline.fetch_api(api_source(), transport=transport)
        url, method, data, headers, timeout = transport.calls[0]
        self.assertEqual((url, method, json.loads(data), headers['Content-Type'], timeout),
                         (API, 'POST', {'body': {}}, 'application/json', 30))
        self.assertNotIn('Cookie', headers)
        self.assertEqual(doc['json'], OK_PAYLOAD)
        self.assertIn('單筆享 10% 回饋', doc['text'])
        self.assertNotIn('<p>', doc['text'])
        reordered = json.loads(json.dumps(OK_PAYLOAD, sort_keys=True))
        reordered = dict(reversed(list(reordered.items())))
        self.assertEqual(canonical_json(reordered), canonical_json(OK_PAYLOAD))

    def test_fetch_api_rejects_bad_sources_and_responses(self):
        big = {'body': {'x': 'a' * 41000}}
        for name, source, transport in [
            ('not ok', api_source(), FakeTransport(payload={**OK_PAYLOAD, 'header': {'returnCode': 'TF9999'}})),
            ('not json', api_source(), FakeTransport(raw=b'<html>busy</html>')),
            ('status', api_source(), FakeTransport(status=500, payload=OK_PAYLOAD)),
            ('http', api_source(url='http://wallet.example/api'), FakeTransport(payload=OK_PAYLOAD)),
            ('credentials', api_source(url='https://u:p@wallet.example/api'), FakeTransport(payload=OK_PAYLOAD)),
            ('body too big', api_source(body=big), FakeTransport(payload=OK_PAYLOAD)),
            ('too large', api_source(), FakeTransport(raw=b'{"a":"' + b'x' * (pipeline.LIMIT + 1) + b'"}')),
            ('method', api_source(method='DELETE'), FakeTransport(payload=OK_PAYLOAD)),
            ('no ok pointer', {k: v for k, v in api_source().items() if k != 'okPointer'}, FakeTransport(payload=OK_PAYLOAD)),
            ('missing ok field', api_source(okPointer='/header/missing'), FakeTransport(payload=OK_PAYLOAD)),
        ]:
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    pipeline.fetch_api(source, transport=transport)

    def test_collect_uses_the_api_fetcher_and_reports_errors(self):
        doc = collect([api_source()], downloader=str, proof_downloader=dict,
                      api_fetcher=lambda s: pipeline.fetch_api(s, transport=FakeTransport(payload=OK_PAYLOAD)))
        self.assertEqual(doc['wallet_api']['fetch'], 'api')
        self.assertEqual(doc['wallet_api']['sha256'], hashlib.sha256(canonical_json(OK_PAYLOAD).encode()).hexdigest())

        def fail(source):
            raise ValueError('timeout')
        self.assertIn('error', collect([api_source()], downloader=str, proof_downloader=dict, api_fetcher=fail)['wallet_api'])
        with self.assertRaises(ValueError):
            collect([api_source(render='chrome')], downloader=str, proof_downloader=dict)
        with self.assertRaises(ValueError):
            collect([api_source(fetch='graphql')], downloader=str, proof_downloader=dict)


def json_catalog():
    """TEST DATA ONLY: the platform test program citing JSON API evidence."""
    catalog = platform_catalog()
    program = catalog['programs'][0]
    select = OK_PAYLOAD['body']['detail']
    program['evidence'] = [{
        'id': 'w.api', 'sourceCard': 'wallet_api', 'url': API, 'role': 'primary', 'format': 'json',
        'select': '/body/detail',
        'approvedSha256': hashlib.sha256(canonical_json(select).encode()).hexdigest(),
        'anchors': {'rate': {'pointer': '/body/detail/content', 'contains': '單筆享 10% 回饋'},
                    'cap': {'pointer': '/body/detail/content', 'contains': '每月上限 200 點'}},
        'approval': {'by': 'test', 'at': '2026-10-26T12:00:00+08:00'}}]
    program['rules'][0]['evidenceRefs'] = ['w.api#rate', 'w.api#cap']
    return catalog


def api_evidence(payload):
    doc = {'url': API, 'json': payload, 'text': json.dumps(payload, ensure_ascii=False), 'fetch': 'api',
           'sha256': hashlib.sha256(canonical_json(payload).encode()).hexdigest(), 'supportingSources': {}}
    return {'wallet_api': doc}


class JsonEvidenceTests(unittest.TestCase):
    def test_json_evidence_is_valid_and_publishes(self):
        validate_catalog(json_catalog())
        published, pending = build_programs(json_catalog(), api_evidence(OK_PAYLOAD), NOW)
        self.assertEqual(pending, {})

    def test_changes_outside_the_selected_subtree_keep_the_rule(self):
        payload = copy.deepcopy(OK_PAYLOAD)
        payload['header']['txSn'] = 'random-2'
        payload['body']['banner'] = '輪播 2'
        self.assertEqual(build_programs(json_catalog(), api_evidence(payload), NOW)[1], {})

    def test_changes_inside_or_missing_pointers_withdraw(self):
        changed = copy.deepcopy(OK_PAYLOAD)
        changed['body']['detail']['end'] = '2027-01-31'
        gone = copy.deepcopy(OK_PAYLOAD)
        del gone['body']['detail']
        for name, payload in (('changed', changed), ('gone', gone)):
            with self.subTest(name):
                self.assertIn('wallet_platform/w.scan', build_programs(json_catalog(), api_evidence(payload), NOW)[1])

    def test_json_evidence_shape_is_checked(self):
        for name, mutate in {
            'no select': lambda e: e.pop('select'),
            'anchor outside select': lambda e: e['anchors']['rate'].__setitem__('pointer', '/header/returnCode'),
            'relative select': lambda e: e.__setitem__('select', 'body/detail'),
            'select on html': lambda e: e.__setitem__('format', 'html'),
        }.items():
            with self.subTest(name):
                catalog = json_catalog()
                mutate(catalog['programs'][0]['evidence'][0])
                with self.assertRaises(ValueError):
                    validate_catalog(catalog)


# ---------------------------------------------------------------- plain downloads

class PlainDownloadSourceTests(unittest.TestCase):
    def test_easywallet_and_icash_are_registered_for_plain_download(self):
        from test_rules_v2_cube import platform_registry
        plain = {s['cardId']: s for s in platform_registry() if s['cardId'] in ('easywallet_friday', 'icash_pay_sunday')}
        self.assertEqual(plain, {
            'easywallet_friday': {'cardId': 'easywallet_friday',
                                  'url': 'https://easywallet.easycard.com.tw/benefit/content?id=1747884970'},
            'icash_pay_sunday': {'cardId': 'icash_pay_sunday', 'url': 'https://www.icashpay.com.tw/advertMessage/view/id/2284'}})
        catalog = load_catalog(FEED / 'programs')
        cited = {e['sourceCard'] for p in catalog['programs'] for e in p['evidence']}
        self.assertFalse(cited & set(plain), 'registered for monitoring only; no approved fingerprint yet')

    def test_chrome_is_refused_where_the_site_blocks_headless_browsers(self):
        for url in ('https://easywallet.easycard.com.tw/benefit/content?id=1', 'https://www.icashpay.com.tw/advertMessage/index'):
            with self.subTest(url):
                with self.assertRaisesRegex(ValueError, 'plain download'):
                    collect([{'cardId': 'x', 'url': url, 'render': 'chrome'}], downloader=str, proof_downloader=dict)


# ---------------------------------------------------------------- discovery

SNAP = FEED.parent / 'audit/v2-s10_20261026'


def replay():
    """Discovery results from the saved official listings (2026-10-26)."""
    import discovery
    html = json.loads((SNAP / 'discovery-html-snapshot.json').read_text())
    tp_list = json.loads((SNAP / 'taiwanpay-list-snapshot.json').read_text())
    tp_details = json.loads((SNAP / 'taiwanpay-details-snapshot.json').read_text())

    def getter(url):
        return html[url]

    def poster(url, body):
        if url.endswith('TF020109'):
            return tp_list
        return {'header': {'returnCode': 'TF0000'}, 'body': {'campaignDetail': tp_details[body['body']['systemSeq']]}}
    return discovery.collect_discovery(discovery.load_sources(FEED), getter=getter, poster=poster, pause=0)


@requires_project_evidence
class ReplayDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = replay()

    def items(self, platform):
        return self.found[platform]['items']

    def test_saved_listings_yield_every_campaign(self):
        self.assertEqual({k: len(v['items']) for k, v in self.found.items()},
                         {'taiwanPay': 86, 'easyWallet': 74, 'icashPay': 8})
        friday = next(i for i in self.items('easyWallet') if i['id'] == '1747884970')
        self.assertEqual((friday['title'], friday['end']), ('週五會員日 最高回饋5%', '2026-12-25'))
        sunday = next(i for i in self.items('icashPay') if i['id'] == '2284')
        self.assertEqual((sunday['title'], sunday['end']), ('週日最好Pay 全通路消費滿額回饋7%', '2026-12-31'))
        for item in [i for v in self.found.values() for i in v['items']]:
            self.assertRegex(item['contentSha256'], '^[0-9a-f]{64}$')
            self.assertTrue(item['sourceUrl'].startswith('https://'))

    def test_tier_a_settles_taiwan_pay_by_facts_only(self):
        import discovery
        result = discovery.classify([i for v in self.found.values() for i in v['items']], known=[], today='2026-10-26')
        tp = [r for r in result['items'] if r['platform'] == 'taiwanPay']
        settled = [r for r in tp if r['tier'] == 'A']
        # The prototype's 84 also settled six location items: four web/app channels (MyCard, Apple
        # Rewards Store, 南農電舖, 跨行轉帳) and two store campaigns whose location also names a website.
        self.assertEqual(len(settled), 78)
        open_items = [r for r in tp if r['tier'] != 'A']

        def find(text):
            return next(r for r in open_items if text in r['title'])
        for text in ('MyCard', 'Apple Rewards', '挺小農', '跨行轉帳'):
            self.assertNotEqual(find(text)['classification'], 'deferred:V2-S9', text)
        for text in ('頂上加碼', 'STUDIO A'):
            # Store campaigns whose location also names a website: suggested, never settled.
            self.assertEqual((find(text)['tier'], find(text)['classification']), ('B', 'deferred:V2-S9'), text)
            self.assertIn('地點含', find(text)['evidence'])
        self.assertEqual({r['rule'] for r in settled},
                         {'ended.period', 'ended.platformMark', 'reverseScan.official', 'restricted.location'})
        by_title = {r['title'][:5]: r for r in tp}
        for title in ('墊腳石Pa', '漢神巨蛋就', '傑昇愛用台', '耐斯購物P', '微風集團【'):
            self.assertEqual((by_title[title]['rule'], by_title[title]['classification']),
                             ('reverseScan.official', 'outOfScope:反掃（出示付款碼）'), title)
        studio = next(r for r in tp if 'STUDIO A' in r['title'])
        self.assertNotEqual(studio['rule'], 'reverseScan.official', 'payment code in one part, website in the other')
        for r in settled:
            self.assertTrue(r['evidence'], r)
        online = next(r for r in tp if 'MyCard' in r['title'])
        self.assertNotEqual(online['tier'], 'A', 'an online channel is never settled as a place')

    def test_official_twqr_exclusions_are_flagged_but_not_settled(self):
        import discovery
        result = discovery.classify([i for v in self.found.values() for i in v['items']], known=[], today='2026-10-26')
        by_id = {(r['platform'], r['id']): r for r in result['items']}
        for key in (('easyWallet', '1780892834'), ('easyWallet', '1780893747'), ('easyWallet', '1790038856'),
                    ('easyWallet', '1790064312'), ('icashPay', '2527')):
            with self.subTest(key):
                # Recorded, never settled: the platform's own non-TWQR stand is still a merchant QR.
                self.assertEqual(by_id[key]['scanMode'], 'nonTwqr')
                self.assertNotEqual(by_id[key]['tier'], 'A')
                self.assertNotIn('非TWQR', by_id[key]['classification'])
        # 大樹藥局 is flagged by its all-TWQR wording, not by the 「其他支付之」 wording that precedes it.
        self.assertEqual(by_id[('easyWallet', '1790038856')]['scanEvidence'], '如掃描TWQR立牌交易')
        for key in (('easyWallet', '1791172316'), ('easyWallet', '1784206763')):
            # 「掃描其他機構TWQR立牌皆不適用」 only limits which stands count; scanning still qualifies.
            self.assertNotEqual(by_id[key]['rule'], 'reverseScan.official', key)

    def test_other_providers_twqr_wording_never_settles(self):
        import discovery
        source = next(s for s in discovery.load_sources(FEED) if s['id'] == 'easyWallet')
        for phrase in ('本活動不包含掃描其他支付之TWQR立牌交易', '掃描其他機構TWQR立牌皆不適用', '其他支付機構之TWQR立牌不適用'):
            with self.subTest(phrase):
                page = f'<p>店家申請Q&A 某店活動 2026-10-01 － 2026-12-31 活動內容 {phrase}</p>' + 'x' * 200
                item = discovery._easywallet(source, lambda url: page if 'id=' in url else 'benefit/content?id=7', None, 0)[0]
                self.assertIsNone(item['scanMode'])

    def test_the_fully_reviewed_known_list_keeps_every_rule_on(self):
        import discovery
        known = json.loads((FEED / 'discovery_known.json').read_text())
        result = discovery.classify([i for v in self.found.values() for i in v['items']], known, '2026-10-26')
        self.assertEqual(result['disabledRules'], [])
        self.assertEqual({r['rule'] for r in result['items']}, {'known.unchanged'})
        self.assertFalse(any('非TWQR' in k['classification'] for k in known))
        self.assertTrue(all(k['by'].startswith('Claude 依官方細則全文判斷') for k in known))

    def test_platform_wide_campaigns_are_never_settled_automatically(self):
        import discovery
        result = discovery.classify([i for v in self.found.values() for i in v['items']], known=[], today='2026-10-26')
        by_id = {(r['platform'], r['id']): r for r in result['items']}
        for key in (('easyWallet', '1747884970'), ('icashPay', '2284')):
            with self.subTest(key):
                self.assertNotEqual(by_id[key]['tier'], 'A')
        self.assertTrue(all(r['tier'] in ('A', 'B', 'C') for r in result['items']))
        self.assertFalse(any(r['classification'].startswith('modeled') for r in result['items']))


class ClassificationTests(unittest.TestCase):
    def item(self, **extra):
        base = {'platform': 'taiwanPay', 'id': 'X1', 'title': '某某夜市享 20% 回饋', 'start': '2026-10-01',
                'end': '2026-12-31', 'location': '台北某某夜市指定店家', 'sourceUrl': 'https://www.taiwanpay.com.tw/x',
                'contentSha256': 'a' * 64}
        return {**base, **extra}

    def classify(self, items, known=(), today='2026-10-26'):
        import discovery
        return discovery.classify(list(items), known=list(known), today=today)

    def test_tier_a_facts(self):
        r = {x['id']: x for x in self.classify([
            self.item(id='nation', location='全國受理台灣Pay之店家'),
            self.item(id='old', end='2026-09-30'),
            self.item(id='full', title='(活動額滿)某某商圈'),
            self.item(id='place'),
            self.item(id='web', location='MyCard網站或MyCard APP'),
            self.item(id='open', location=''),
        ])['items']}
        self.assertEqual((r['old']['tier'], r['old']['classification']), ('A', 'ended'))
        self.assertEqual((r['full']['tier'], r['full']['classification']), ('A', 'ended'))
        self.assertEqual((r['place']['tier'], r['place']['classification'], r['place']['rule']),
                         ('A', 'deferred:V2-S9', 'restricted.location'))
        self.assertIn('台北某某夜市指定店家', r['place']['evidence'])
        self.assertNotEqual(r['web']['tier'], 'A')
        self.assertNotEqual(r['open']['tier'], 'A')
        self.assertNotEqual(r['nation']['tier'], 'A', 'a nationwide location restricts nothing')

    def test_human_classification_holds_only_while_content_is_unchanged(self):
        known = [{'platform': 'taiwanPay', 'id': 'X1', 'title': 't', 'contentSha256': 'a' * 64,
                  'classification': 'outOfScope:線上通路', 'by': 'user', 'at': '2026-10-26'}]
        same = self.classify([self.item(location='')], known)['items'][0]
        self.assertEqual((same['tier'], same['classification'], same['rule']), ('A', 'outOfScope:線上通路', 'known.unchanged'))
        changed = self.classify([self.item(location='', contentSha256='b' * 64)], known)['items'][0]
        self.assertNotEqual(changed['rule'], 'known.unchanged')
        self.assertTrue(changed['changed'])

    def test_tier_b_only_suggests_and_conflicts_go_to_people(self):
        r = {x['id']: x for x in self.classify([
            self.item(platform='easyWallet', id='e1', title='樂天新戶綁定悠遊付好禮', location=''),
            self.item(platform='easyWallet', id='e2', title='某銀行信用卡綁定悠遊付 新戶加碼', location=''),
            self.item(platform='easyWallet', id='e3', title='週五會員日 最高回饋5%', location=''),
        ])['items']}
        self.assertEqual((r['e1']['tier'], r['e1']['classification']), ('B', 'outOfScope:新戶／任務'))
        self.assertIn('新戶', r['e1']['evidence'])
        self.assertEqual(r['e2']['tier'], 'C', 'two suggestion rules disagree')
        self.assertEqual(r['e3']['tier'], 'C')
        self.assertEqual(r['e3']['classification'], 'unclassified')

    def test_a_tier_rule_that_contradicts_the_golden_sample_is_disabled(self):
        known = [{'platform': 'taiwanPay', 'id': 'G1', 'title': 't', 'contentSha256': 'c' * 64,
                  'classification': 'outOfScope:線上通路', 'by': 'user', 'at': '2026-10-26'}]
        # The person said G1 (which carries a location) is an online channel: the location rule is wrong for it.
        result = self.classify([self.item(id='G1', contentSha256='c' * 64), self.item(id='X2')], known)
        self.assertIn('restricted.location', result['disabledRules'])
        x2 = next(r for r in result['items'] if r['id'] == 'X2')
        self.assertNotEqual(x2['tier'], 'A')
        self.assertEqual(x2['classification'], 'deferred:V2-S9', 'demoted to a suggestion, not dropped')

    def test_diff_reports_new_changed_gone_with_first_seen(self):
        import discovery
        known = [{'platform': 'taiwanPay', 'id': 'K1', 'title': 'k', 'contentSha256': 'a' * 64,
                  'classification': 'ended', 'by': 'user', 'at': '2026-10-01'},
                 {'platform': 'taiwanPay', 'id': 'K2', 'title': 'k2', 'contentSha256': 'a' * 64,
                  'classification': 'ended', 'by': 'user', 'at': '2026-10-01'}]
        previous = {'items': [{'platform': 'taiwanPay', 'id': 'N1', 'firstSeen': '2026-10-10'}]}
        classified = self.classify([self.item(id='K1', contentSha256='b' * 64), self.item(id='N1', location=''),
                                    self.item(id='N2', location='')], known)
        report = discovery.diff(classified, known, previous, today='2026-10-26')
        self.assertEqual([i['id'] for i in report['changed']], ['K1'])
        self.assertEqual(sorted(i['id'] for i in report['new']), ['N1', 'N2'])
        self.assertEqual([i['id'] for i in report['gone']], ['K2'])
        first = {i['id']: i['firstSeen'] for i in report['new']}
        self.assertEqual(first, {'N1': '2026-10-10', 'N2': '2026-10-26'})
        summary = discovery.summary_markdown(report, sources={'taiwanPay': {}}, today='2026-10-26')
        self.assertIn('⚠', summary)
        self.assertIn('N1', summary)

    def test_failed_sources_are_reported_without_items(self):
        import discovery

        def broken(url):
            raise ValueError('timeout')
        found = discovery.collect_discovery(discovery.load_sources(FEED), getter=broken,
                                            poster=lambda u, b: (_ for _ in ()).throw(ValueError('busy')), pause=0)
        self.assertEqual(set(found), {'taiwanPay', 'easyWallet', 'icashPay'})
        self.assertTrue(all('error' in v and not v['items'] for v in found.values()))
        summary = discovery.summary_markdown(discovery.diff(discovery.classify([], [], '2026-10-26'), [], None, '2026-10-26'),
                                             sources=found, today='2026-10-26')
        self.assertIn('timeout', summary)

    def test_known_list_is_the_fully_reviewed_list(self):
        known = json.loads((FEED / 'discovery_known.json').read_text())
        self.assertEqual(len(known), 168)
        self.assertEqual(len({(k['platform'], k['id']) for k in known}), 168)
        for k in known:
            self.assertRegex(k['contentSha256'], '^[0-9a-f]{64}$')
            # User 2026-10-26: classifications decidable from the terms are Claude's to make.
            self.assertTrue(k['by'].startswith('Claude 依官方細則全文判斷'))
            self.assertFalse(k['classification'].startswith('modeled'), 'nothing is priced by classification')
        by_id = {(k['platform'], k['id']): k['classification'] for k in known}
        self.assertEqual(by_id[('easyWallet', '1747884970')], 'deferred:V2-S11')
        self.assertEqual(by_id[('icashPay', '2284')], 'deferred:V2-S11')
        self.assertEqual(by_id[('easyWallet', '1785736053')], 'deferred:V2-S9')



class RunDiscoveryTests(unittest.TestCase):
    def test_discovery_never_fails_the_run_and_writes_the_actions_summary(self):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile('r', suffix='.md', delete=False) as handle:
            path = handle.name
        old = os.environ.get('GITHUB_STEP_SUMMARY')
        os.environ['GITHUB_STEP_SUMMARY'] = path
        try:
            def broken(sources):
                raise RuntimeError('listing parser crashed')
            report, sources = pipeline.run_discovery(FEED, '2026-10-26T06:00:00+00:00', collector=broken,
                                                     previous_loader=lambda: None)
            self.assertIn('listing parser crashed', report['error'])
            fake = {'taiwanPay': {'listUrl': 'https://x', 'items': [
                {'platform': 'taiwanPay', 'id': 'N1', 'title': '某某夜市 20%', 'end': '2026-12-31',
                 'location': '某某夜市', 'sourceUrl': 'https://x', 'contentSha256': 'a' * 64}]}}
            report, sources = pipeline.run_discovery(FEED, '2026-10-26T06:00:00+00:00', collector=lambda s: fake,
                                                     previous_loader=lambda: {'items': []})
            self.assertEqual([i['id'] for i in report['new']], ['N1'])
            self.assertEqual(report['new'][0]['tier'], 'A')
            text = Path(path).read_text()
            self.assertIn('發現流程失敗', text)
            self.assertIn('新出現 1', text)
        finally:
            if old is None:
                os.environ.pop('GITHUB_STEP_SUMMARY')
            else:
                os.environ['GITHUB_STEP_SUMMARY'] = old
            os.unlink(path)


class ReviewRound1Tests(unittest.TestCase):
    """Code review round 1: the golden sample, fetch errors, carried items, budgets and periods."""

    def item(self, **extra):
        return {'platform': 'taiwanPay', 'id': 'X1', 'title': '某某夜市享 20% 回饋', 'end': '2026-12-31',
                'location': '台北某某夜市指定店家', 'sourceUrl': 'https://www.taiwanpay.com.tw/x', 'contentSha256': 'a' * 64,
                **extra}

    def known(self, **extra):
        return {'platform': 'taiwanPay', 'id': 'X1', 'title': 't', 'contentSha256': 'a' * 64,
                'classification': 'deferred:V2-S9', 'by': 'user', 'at': '2026-10-26', **extra}

    def test_only_the_deciding_fact_as_of_confirmation_is_compared(self):
        import discovery
        # Ended and located: the person says "ended"; the location rule did not decide, so it stays on.
        ended = discovery.classify([self.item(id='E1', end='2026-09-30'), self.item(id='X2')],
                                   [self.known(id='E1', classification='ended')], '2026-10-26')
        self.assertEqual(ended['disabledRules'], [])
        # A confirmed place campaign that later passes its end date does not disable ended.period.
        later = discovery.classify([self.item(), self.item(id='X3', end='2026-11-30')], [self.known()], '2026-12-15')
        self.assertEqual(later['disabledRules'], [])
        self.assertEqual(next(r for r in later['items'] if r['id'] == 'X3')['rule'], 'ended.period')
        # A changed item is not a golden sample.
        changed = discovery.classify([self.item(contentSha256='b' * 64)],
                                     [self.known(classification='outOfScope:線上通路')], '2026-10-26')
        self.assertEqual(changed['disabledRules'], [])

    @requires_project_evidence
    def test_the_draft_known_list_keeps_tier_a_rules_on(self):
        import discovery
        draft = json.loads((SNAP / 'discovery_known.DRAFT.json').read_text())
        items = [i for v in json.loads((SNAP / 'live-discovery-20261026.json').read_text()).values() for i in v['items']]
        for today in ('2026-10-26', '2026-12-01', '2027-04-01'):
            with self.subTest(today):
                self.assertEqual(discovery.classify(items, draft, today)['disabledRules'], [])

    def test_a_failed_detail_keeps_the_known_classification(self):
        import discovery
        result = discovery.classify([self.item(title='', contentSha256=None, error='HTTP 502')],
                                    [self.known(title='某某夜市')], '2026-10-26')['items'][0]
        self.assertEqual((result['tier'], result['classification'], result['changed'], result['fetchError']),
                         ('A', 'deferred:V2-S9', False, True))
        self.assertEqual(result['title'], '某某夜市')
        unknown = discovery.classify([self.item(contentSha256=None, error='HTTP 502')], [], '2026-10-26')['items'][0]
        self.assertEqual((unknown['tier'], unknown['classification']), ('C', 'unclassified'))

    def test_a_failed_platform_carries_yesterdays_items_and_first_seen(self):
        import discovery
        previous = {'items': [{'platform': 'easyWallet', 'id': 'P1', 'title': 'p', 'tier': 'C', 'firstSeen': '2026-10-10'}]}
        report = discovery.diff(discovery.classify([], [], '2026-10-26'), [], previous, '2026-10-26', failed={'easyWallet'})
        self.assertEqual([(i['id'], i['firstSeen'], i['carried']) for i in report['items']], [('P1', '2026-10-10', True)])
        self.assertEqual(report['gone'], [])

    def test_an_empty_listing_is_a_source_error(self):
        import discovery
        found = discovery.collect_discovery(discovery.load_sources(FEED), getter=lambda url: '<html>維護中</html>',
                                            poster=lambda u, b: {'header': {'returnCode': 'TF0000'}, 'body': {}}, pause=0)
        self.assertTrue(all('no campaigns' in v['error'] for v in found.values()), found)

    def test_the_time_budget_stops_discovery(self):
        import discovery
        ticks = iter(range(0, 10000, 100))
        found = discovery.collect_discovery(discovery.load_sources(FEED), getter=lambda url: 'benefit/content?id=1 advertMessage/view/id/2',
                                            poster=lambda u, b: {'body': {'normalCampaigns': [{'systemSeq': 'S1'}]}},
                                            pause=0, budget=150, clock=lambda: next(ticks))
        self.assertTrue(all('budget' in v['error'] for v in found.values()), found)

    def test_taiwan_pay_falls_back_to_the_listing_end_date(self):
        import discovery
        source = next(s for s in discovery.load_sources(FEED) if s['id'] == 'taiwanPay')
        items = discovery._taiwanpay(source, None, lambda u, b: (
            {'body': {'normalCampaigns': [{'systemSeq': 'S1', 'title': 't', 'endDate': '2026-09-30'}]}}
            if u.endswith('TF020109') else {'body': {'campaignDetail': {'title': 't'}}}), 0)
        self.assertEqual(items[0]['end'], '2026-09-30')

    def test_only_the_period_at_its_fixed_place_is_read(self):
        import discovery
        page = ('<p>好康特區 店家申請Q&A 某活動 回饋</p><p>領券期間 2026/01/01 ~ 2026/01/31</p>'
                '<p>活動辦法 2026-10-01 － 2026-12-31</p>' + 'x' * 200)
        source = next(s for s in discovery.load_sources(FEED) if s['id'] == 'easyWallet')
        items = discovery._easywallet(source, lambda url: page if 'id=' in url else 'benefit/content?id=7', None, 0)
        self.assertIsNone(items[0]['end'], 'a body range is never taken as the campaign period')
        icash = next(s for s in discovery.load_sources(FEED) if s['id'] == 'icashPay')
        page = '<p>活動訊息 某活動 發布時間 : 2026-07-01</p><p>兌換期限 2026/01/01~2026/02/01</p><p>活動期間： 2026/7/1 ~ 2026/12/31</p>'
        items = discovery._icash(icash, lambda url: page if 'view' in url else 'advertMessage/view/id/9', None, 0)
        self.assertEqual(items[0]['end'], '2026-12-31')


if __name__ == '__main__':
    unittest.main()
