import copy
import json
import unittest
from pathlib import Path
from datetime import datetime, timezone
from pipeline import build_feed, normalize_aem_model
from eligibility_audit import build_eligibility_audit, RESPONSIBILITY


class EligibilityAuditTests(unittest.TestCase):
    def feed(self):
        catalog=json.loads((Path(__file__).resolve().parents[1]/'catalog.json').read_text())
        return build_feed([],{},catalog,datetime(2026,10,23,tzinfo=timezone.utc))

    def test_every_pending_source_and_condition_remains_visible(self):
        feed=self.feed();audit=build_eligibility_audit(feed,{})
        self.assertEqual(audit['sourceCount'],20)
        self.assertEqual(audit['notModeledCount'],20)
        self.assertEqual(audit['fullyVerifiedEntitlementCount'],0)
        for row in audit['sources']:
            self.assertFalse(row['transactionEligibilityVerified'])
            self.assertFalse(row['fullCardCoverage'])
            self.assertTrue(row['unresolvedRuleGaps'])
            self.assertTrue(row['conditions'])
            for c in row['conditions']:
                self.assertEqual(c['responsibility'],RESPONSIBILITY[c['evidence']])
                self.assertEqual(c['status'],'requiresCurrentEvidence')

    def test_missing_source_or_expired_order_cannot_silently_hide_a_gap(self):
        feed=self.feed()
        for mutate in [lambda f:f['coverage'].pop(),
                       lambda f:f.update(expiresAt=f['generatedAt']),
                       lambda f:f['assessmentProfiles'].pop('fubon_j')]:
            broken=copy.deepcopy(feed);mutate(broken)
            with self.assertRaises(ValueError):build_eligibility_audit(broken,{})

    def test_debit_official_model_never_parses_as_credit_card_model(self):
        data=json.dumps({'title':'CUBE 簽帳金融卡',':items':{'main':{'text':'一般消費0.3%'}}}).encode()
        with self.assertRaises(ValueError):normalize_aem_model(data)
        text=normalize_aem_model(data,expected_title='CUBE 簽帳金融卡')
        self.assertIn('0.3%',text)
        self.assertIn('official-model-sha256',text)
