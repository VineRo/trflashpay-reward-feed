import json
import unittest
from pathlib import Path
from unittest.mock import patch
from assessment_profiles import build_profiles, validate_profile

class ProfileTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]
    def test_twenty_profiles_explicitly_limit_scope_and_categorize_unknowns(self):
        catalog=json.loads((self.root/'catalog.json').read_text())
        profiles=build_profiles({},[],catalog)
        self.assertCountEqual(profiles,catalog)
        for cid,p in profiles.items():
            validate_profile(p,cid)
            self.assertFalse(p['fullCardCoverage'])
            self.assertFalse(p['announcementObserved'])
            self.assertTrue(p['unmodeledBenefits'])
            self.assertIsNone(p['calculation'])
    def test_changed_evidence_revokes_calculation_but_retains_observation(self):
        rule={'cardId':'jkopay_card','promoRewardRate':1,'validUntil':'2026-12-31T23:59:59.999999+08:00'}
        source={'sha256':'a'*64,'url':'https://www.taishinbank.com.tw/terms'}
        with patch.dict('assessment_profiles.APPROVALS',{'jkopay_card':'a'*64},clear=True):
            p=build_profiles({'jkopay_card':source},[rule],['jkopay_card'])['jkopay_card']
            validate_profile(p,'jkopay_card')
            self.assertEqual(p['calculation']['rounding'],'halfUp')
            source['sha256']='b'*64
            changed=build_profiles({'jkopay_card':source},[rule],['jkopay_card'])['jkopay_card']
            self.assertTrue(changed['announcementObserved'])
            self.assertIsNone(changed['calculation'])
    def test_no_invented_complete_or_missing_conditions(self):
        import copy
        p=build_profiles({},[],['ctbc_linepay'])['ctbc_linepay']
        for values in [{'fullCardCoverage':True},{'conditions':[]},{'unmodeledBenefits':[]},{'sourceSha256':'bad'}]:
            bad=copy.deepcopy(p);bad.update(values)
            with self.assertRaises(ValueError):validate_profile(bad,'ctbc_linepay')

    def test_supporting_document_changes_revoke_amount_calculation(self):
        from assessment_profiles import SUPPORT_APPROVALS
        url=next(iter(SUPPORT_APPROVALS['fubon_costco']))
        expected=SUPPORT_APPROVALS['fubon_costco'][url]
        rule={'cardId':'fubon_costco','promoRewardRate':1,'validUntil':'2027-12-31T23:59:59.999999+08:00'}
        source={'url':'https://www.fubon.com/terms','sha256':'a'*64,'supportingSources':{url:{'sha256':expected}}}
        with patch.dict('assessment_profiles.APPROVALS',{'fubon_costco':'a'*64},clear=True):
            original=build_profiles({'fubon_costco':source},[rule],['fubon_costco'])['fubon_costco']
            validate_profile(original,'fubon_costco')
            self.assertEqual(original['calculation']['rounding'],'halfUp')
            source['supportingSources'][url]={'sha256':'b'*64}
            changed=build_profiles({'fubon_costco':source},[rule],['fubon_costco'])['fubon_costco']
            self.assertNotEqual(original['sourceSha256'],changed['sourceSha256'])
            self.assertIsNone(changed['calculation'])
            source['supportingSources'][url]={'error':'unavailable'}
            missing=build_profiles({'fubon_costco':source},[rule],['fubon_costco'])['fubon_costco']
            self.assertTrue(missing['announcementObserved'])
            self.assertIsNone(missing['calculation'])
