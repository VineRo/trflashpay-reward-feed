import copy
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from wallet_campaigns import build_wallet_campaigns, APPROVALS, URLS, DEFINITIONS


class WalletCampaignTests(unittest.TestCase):
    now=datetime(2026,10,23,tzinfo=timezone.utc)
    def evidence(self):
        return {sid:{'url':URLS[sid],'sha256':sha,'text':'Current official terms'} for sid,sha in APPROVALS.items()}
    def test_all_four_are_fixed_or_task_awards_not_base_percentages(self):
        campaigns,pending=build_wallet_campaigns(self.evidence(),self.now)
        self.assertEqual(len(campaigns),4)
        self.assertFalse(pending)
        for c in campaigns:
            self.assertNotIn('rate',c)
            self.assertEqual(c['stacking'],'separateOnly')
            self.assertEqual(c['availability'],'requiresConfirmation')
            self.assertIn('unusedGrant',{r['id'] for r in c['requirements']})
            self.assertIn('poolAvailable',{r['id'] for r in c['requirements']})
    def test_funding_rails_and_banks_never_interchange(self):
        campaigns,_=build_wallet_campaigns(self.evidence(),self.now)
        byid={c['campaignId']:c for c in campaigns}
        easy=byid['easy_yuanta_tuition_2026'];obank=byid['ipass_obank_new_debit_2026']
        self.assertEqual(easy['fundingTypes'],['bankAccount'])
        self.assertEqual(easy['fundingBankId'],'yuanta')
        self.assertEqual((easy['minimumAmount'],easy['rewardAmount']),(5999,100))
        self.assertEqual(obank['fundingTypes'],['debitCard'])
        self.assertEqual(obank['requiredTransactions'],3)
        self.assertEqual(obank['fundingBankId'],'obank')
        self.assertEqual(obank['rewardAmount'],600)
        for c in campaigns[:2]:
            self.assertEqual(set(c['fundingTypes']),{'creditCard','bankAccount','storedBalance'})
            self.assertEqual(c['grantScope'],'calendarMonthly')
    def test_changed_or_missing_source_withdraws_its_campaigns_only(self):
        for change in [{'sha256':'0'*64},{'error':'unavailable'},{'url':'https://example.invalid/terms'}]:
            e=self.evidence();e['wallet_px_income'].update(change)
            campaigns,pending=build_wallet_campaigns(e,self.now)
            self.assertEqual(len(campaigns),2)
            self.assertEqual(len(pending),2)
    def test_confirmed_exhaustion_and_dates_stop_awards(self):
        e=self.evidence();e['wallet_easy_yuanta']['text']='【已額滿】'
        campaigns,pending=build_wallet_campaigns(e,self.now)
        self.assertEqual(len(campaigns),3)
        self.assertIn('額滿',pending['easy_yuanta_tuition_2026'])
        campaigns,pending=build_wallet_campaigns(self.evidence(),datetime(2027,1,1,tzinfo=timezone.utc))
        self.assertFalse(campaigns)
        self.assertEqual(len(pending),4)
    def test_without_wallet_evidence_no_synthetic_wallet_bonus_is_published(self):
        campaigns,pending=build_wallet_campaigns({},self.now)
        self.assertFalse(campaigns)
        self.assertEqual(len(pending),len(DEFINITIONS))

    def test_unreviewed_amount_funding_or_condition_mutations_are_rejected(self):
        from wallet_campaigns import validate_wallet_campaign
        campaigns,_=build_wallet_campaigns(self.evidence(),self.now)
        for key,value in [('rewardAmount',999),('fundingTypes',['creditCard']),('requirements',[])]:
            changed=copy.deepcopy(campaigns[2]);changed[key]=value
            with self.assertRaises(ValueError):validate_wallet_campaign(changed)
