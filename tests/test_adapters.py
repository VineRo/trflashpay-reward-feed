import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from adapters import ctbc, ubear, observe


class AdapterTests(unittest.TestCase):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)

    def ctbc_source(self, value='1'):
        return {'url': 'https://www.ctbcbank.com/terms', 'text':
            f'國內外一般消費 國內外一般刷卡交易，不含本行 不回饋交易項目 。 {value}% 1% '
            f'國內外一般消費{value}% 臺灣 LINE Pay 分期付款交易恕不適用 四捨五入至整數 '
            'LINE Pay信用卡自2026/7/1~2026/12/31為止'}

    def ubear_source(self, total='3', cap='150'):
        return {'url': 'https://event.esunbank.com.tw/terms', 'text':
            f'2026/9/1 ~ 2027/2/28 國內外一般消費1%現金回饋，需同時 '
            f'行動支付、網路消費 最高 {total}% 現金回饋 '
            f'網路消費加碼2%回饋採正附卡合併計算， 每期回饋上限{cap}元 '
            '帳單e化 自動扣繳 不列入網路消費回饋計算 無條件捨去'}

    def test_ctbc_updates_explicit_rate_without_qualifying_transaction(self):
        for value in ['1', '2']:
            rule = ctbc(self.ctbc_source(value), self.now)
            self.assertEqual(rule['promoRewardRate'], float(value))
            self.assertTrue(rule['verificationRequiredReason'])
            self.assertEqual(rule['defaultRewardRate'], 0)

    def test_ctbc_rejects_contradictory_rate(self):
        source = self.ctbc_source()
        source['text'] += ' 國內外一般消費9%'
        with self.assertRaises(ValueError):
            ctbc(source, self.now)

    def test_ubear_extracts_bonus_cap_not_subscription_cap(self):
        source = self.ubear_source()
        source['text'] += ' 指定訂閱最高10%每期回饋上限100元'
        rule = ubear(source, self.now)
        self.assertEqual(rule['promoRewardRate'], 3)
        self.assertTrue(any('150' in tag for tag in rule['conditionTags']))
        self.assertTrue(rule['verificationRequiredReason'])

    def test_ubear_updates_explicit_cap(self):
        rule = ubear(self.ubear_source(cap='200'), self.now)
        self.assertTrue(any('200' in tag for tag in rule['conditionTags']))

    def test_ubear_rejects_inconsistent_total_or_zero_cap(self):
        for source in [self.ubear_source(total='8'), self.ubear_source(cap='0')]:
            with self.assertRaises(ValueError):
                ubear(source, self.now)

    def test_missing_or_expired_terms_remain_pending(self):
        rules, errors = observe({'ctbc_linepay': self.ctbc_source()}, datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(rules, [])
        self.assertIn('ctbc_linepay', errors)
        self.assertIn('esun_ubear', errors)

class ExtendedAdapterTests(unittest.TestCase):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)

    def fubon_source(self, rate='1', fallback='0.5'):
        return {'url': 'https://www.fubon.com/terms', 'text':
            '基本回饋 優惠期間自 2026/4/1 至 2026/12/31 止 '
            f'國內及其他海外地區一般消費享最高{rate}% '
            f'尚未生效或無持續符合條件，則享{fallback}%回饋 '
            'J Cash J Points 所有消費回饋皆須綁定LINE Pay後生效 '
            '電子帳單 自動扣繳 無條件捨去至整數 日本最高6%'}

    def test_fubon_does_not_select_travel_marketing_maximum(self):
        from adapters import fubon_j
        rule = fubon_j(self.fubon_source(), self.now)
        self.assertEqual(rule['promoRewardRate'], 1)
        self.assertTrue(rule['verificationRequiredReason'])
        self.assertEqual(rule['defaultRewardRate'], 0)

    def test_fubon_accepts_identical_desktop_mobile_sections(self):
        from adapters import fubon_j
        source = self.fubon_source()
        source['text'] *= 2
        self.assertEqual(fubon_j(source, self.now)['promoRewardRate'], 1)

    def test_fubon_rejects_conflicting_sections_and_fallback(self):
        from adapters import fubon_j
        for source in [self.fubon_source(fallback='9'),
                       {'url': 'https://www.fubon.com/terms', 'text': self.fubon_source()['text'] + self.fubon_source(rate='2')['text']}]:
            with self.assertRaises(ValueError):
                fubon_j(source, self.now)

    def test_debit_uses_debit_table_and_own_validity(self):
        from adapters import ctbc_debit
        source = AdapterTests().ctbc_source()
        source['text'] += ' LINE Pay簽帳金融卡自2026/7/1~2026/11/30'
        rule = ctbc_debit(source, self.now)
        self.assertEqual(rule['cardType'], 'debitCard')
        self.assertEqual(rule['cardId'], 'ctbc_linepay_debit')
        self.assertIn('2026-11-30', rule['validUntil'])
        self.assertTrue(rule['verificationRequiredReason'])

class ConditionalMetadataTests(unittest.TestCase):
    def test_conditional_metadata_preserves_own_dates_and_rate(self):
        from adapters import ctbc
        source = AdapterTests().ctbc_source()
        source['sha256'] = 'reviewed-fixture'
        with patch.dict('adapters.CALCULATION_APPROVALS', {'ctbc_linepay': 'reviewed-fixture'}, clear=True):
            rule = ctbc(source, AdapterTests.now)
        self.assertEqual(rule['issuerTerms']['rate'], rule['promoRewardRate'])
        self.assertEqual(rule['issuerTerms']['validUntil'], rule['validUntil'])
        self.assertEqual(rule['issuerTerms']['rounding'], 'halfUp')
        self.assertTrue(rule['verificationRequiredReason'])

    def test_invalid_conditional_metadata_never_publishes(self):
        import copy
        from adapters import ctbc
        from validator import validate
        source = AdapterTests().ctbc_source()
        source['sha256'] = 'reviewed-fixture'
        with patch.dict('adapters.CALCULATION_APPROVALS', {'ctbc_linepay': 'reviewed-fixture'}, clear=True):
            rule = ctbc(source, AdapterTests.now)
        for fields in [{'rate': 9}, {'version': True}, {'rounding': 'floor'},
                       {'validFrom': '2027-01-01T00:00:00+08:00'}, {'unknown': True}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                bad = copy.deepcopy(rule)
                bad['issuerTerms'].update(fields)
                validate(bad)
        rule['verificationRequiredReason'] = None
        with self.assertRaises(ValueError):
            validate(rule)

    def test_changed_terms_keep_observation_but_revoke_calculation(self):
        from adapters import ctbc
        source = AdapterTests().ctbc_source()
        source['sha256'] = 'unreviewed-change'
        rule = ctbc(source, AdapterTests.now)
        self.assertEqual(rule['promoRewardRate'], 1)
        self.assertNotIn('issuerTerms', rule)
        self.assertTrue(rule['verificationRequiredReason'])
