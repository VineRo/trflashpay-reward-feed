import copy
import json
import unittest
from cube_scheme_inventory import parse_scheme_inventory, reviewed_scheme_inventory


def model():
    return {'title':'CUBE卡權益方案', ':items':{'scheme':{
        'anchorKey':'fixed', 'mainTitle':'<h3>固定回饋 1.2%</h3>',
        'paragraphNoticeContent':'<p>僅適用指定條件；排除項目</p>',
        ':items':{'channel':{'categoryName':'一般消費',
            'noticeContent':'舊版條款仍保留待人工核對',
            'contentTrees':[{'description':'例外條件','title':'通路'}],
            'script':'fake 99%', 'image':'https://invalid/50%',
            'text':'<script>fake 100%</script>現行說明'}}}}}


class CubeSchemeInventoryTests(unittest.TestCase):
    def encoded(self, value):
        return json.dumps(value, ensure_ascii=False).encode()

    def test_structured_terms_channels_and_legacy_fields_keep_provenance(self):
        result = parse_scheme_inventory(self.encoded(model()))
        scheme = result['schemes'][0]
        self.assertIn('指定條件',scheme['notice'])
        fields = scheme['authoredFields']
        self.assertTrue(any(x['field']=='categoryName' for x in fields))
        self.assertTrue(any('contentTrees/0/description' in x['path'] for x in fields))
        self.assertTrue(any('舊版條款' in x['text'] for x in fields))
        self.assertNotIn('fake',str(fields))
        self.assertNotIn('https://invalid',str(fields))
        self.assertIsNone(scheme['calculation'])
        self.assertFalse(result['fullCardCoverage'])

    def test_every_authored_and_unknown_field_change_withdraws_old_approval(self):
        original = model()
        digest = parse_scheme_inventory(self.encoded(original))['modelSha256']
        self.assertIsNotNone(reviewed_scheme_inventory(self.encoded(original),approved_sha256=digest))
        for field in ['mainTitle','paragraphNoticeContent','newEligibilityField']:
            changed = copy.deepcopy(original)
            changed[':items']['scheme'][field] = '新的限制'
            self.assertIsNone(reviewed_scheme_inventory(self.encoded(changed),approved_sha256=digest))
        changed=copy.deepcopy(original)
        changed[':items']['scheme'][':items']['channel']['categoryName']='新通路'
        self.assertIsNone(reviewed_scheme_inventory(self.encoded(changed),approved_sha256=digest))
        self.assertIsNone(reviewed_scheme_inventory(self.encoded(original),approved_sha256=None))

    def test_key_order_does_not_withdraw_same_content(self):
        original=model()
        reordered=dict(reversed(list(original.items())))
        self.assertEqual(parse_scheme_inventory(self.encoded(original)),parse_scheme_inventory(self.encoded(reordered)))

    def test_missing_or_wrong_typed_terms_and_channels_are_rejected(self):
        for key in ['anchorKey','mainTitle','paragraphNoticeContent',':items']:
            for replacement in [None, [], '']:
                changed=model();changed[':items']['scheme'][key]=replacement
                with self.assertRaises(ValueError):parse_scheme_inventory(self.encoded(changed))
        for value in [{}, [], {'title':'Other',':items':{}}, {'title':'CUBE卡權益方案',':items':{}}]:
            with self.assertRaises(ValueError):parse_scheme_inventory(self.encoded(value))

    def test_duplicate_keys_anchors_and_nonfinite_values_are_rejected(self):
        with self.assertRaises(ValueError):parse_scheme_inventory('{"title":"a","title":"b"}')
        with self.assertRaises(ValueError):parse_scheme_inventory('{"x":NaN}')
        changed=model();changed[':items']['duplicate']=copy.deepcopy(changed[':items']['scheme'])
        with self.assertRaises(ValueError):parse_scheme_inventory(self.encoded(changed))

    def test_deep_unknown_fields_are_rejected(self):
        changed=model();nested={}
        for _ in range(42):nested={'child':nested}
        changed['unknown']=nested
        with self.assertRaises(ValueError):parse_scheme_inventory(self.encoded(changed))
