import json
import unittest
from datetime import datetime, timezone
from pipeline import normalize_aem_model
from adapters import cube_fixed_domestic


class CubeModelTests(unittest.TestCase):
    terms = ('「固定回饋」權益方案適用期間：2026/8/3-2026/12/31。 '
             '一般消費(不含保費)1.2%、海外實體消費2.5%。 '
             '每筆消費請款入帳後依該筆消費之授權日判斷適用權益方案，'
             '小數點第一位四捨五入至整數位。信用額度加計50萬元。不含CUBE簽帳金融卡。')

    def test_only_authored_text_is_normalized_and_metadata_order_is_stable(self):
        model = {'title':'CUBE信用卡', ':items':{'main':{'text':'<p>'+self.terms+'</p>',
            'image':'https://example.invalid/99%', 'script':'fake rate 30%',
            'description':'<script>fake 99%</script>正常條款'}}, 'lastModifiedDate':123}
        text = normalize_aem_model(json.dumps(model).encode())
        self.assertIn('正常條款', text)
        self.assertNotIn('fake', text)
        self.assertNotIn('example.invalid', text)
        changed = dict(reversed(list(model.items())))
        changed['lastModifiedDate'] = 999
        self.assertEqual(text, normalize_aem_model(json.dumps(changed).encode()))

    def test_other_json_or_deep_models_are_rejected(self):
        for model in [{}, [], {'title':'Other', ':items':{}}]:
            with self.assertRaises(ValueError):
                normalize_aem_model(json.dumps(model).encode())
        model = {'text': self.terms}
        for _ in range(42):
            model = {'child': model}
        with self.assertRaises(ValueError):
            normalize_aem_model(json.dumps({'title':'CUBE信用卡', ':items':model}).encode())

    def test_scoped_rate_requires_current_period_rounding_and_issuer_conditions(self):
        def parse(text, now=datetime(2026,10,23,tzinfo=timezone.utc)):
            return cube_fixed_domestic({'text':text, 'url':'https://www.cathay-cube.com.tw/model.json'},now)
        rule = parse(self.terms)
        self.assertEqual(rule['promoRewardRate'],1.2)
        self.assertTrue(rule['verificationRequiredReason'])
        self.assertNotIn('.model.json',rule['officialUrl'])
        for text in [self.terms.replace('小數點第一位四捨五入至整數位',''),
                     self.terms + '一般消費(不含保費)2%、海外實體消費2.5%。']:
            with self.assertRaises(ValueError): parse(text)
        with self.assertRaises(ValueError):
            parse(self.terms,datetime(2027,1,1,tzinfo=timezone.utc))
