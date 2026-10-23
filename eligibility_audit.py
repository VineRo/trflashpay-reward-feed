"""Publish every unresolved eligibility dependency without claiming entitlement."""
from datetime import datetime
from assessment_profiles import validate_profile, source_digest

RESPONSIBILITY = {
    'userProfile': '使用者提供當期有效資料',
    'transaction': '使用者提供本次交易與付款路徑',
    'issuerDecision': '銀行或平台實際交易認列',
    'quota': '使用者提供已用額度，平台確認總量名額',
}


def build_eligibility_audit(feed, evidence):
    generated = datetime.fromisoformat(feed['generatedAt'])
    expiry = datetime.fromisoformat(feed['expiresAt'])
    if generated.tzinfo is None or expiry.tzinfo is None or not generated < expiry:
        raise ValueError('Audit requires the same bounded feed lease')
    coverage = feed['coverage']
    if len(coverage) != len(set(coverage)) or set(coverage) != set(feed['assessmentProfiles']):
        raise ValueError('Every catalog source requires an audit')
    entries = []
    for cid in coverage:
        p = feed['assessmentProfiles'][cid]
        validate_profile(p, cid)
        source = evidence.get(cid, {})
        readable = bool(source.get('text')) and not source.get('error')
        calc = p['calculation']
        if calc:
            supporting = source.get('supportingSources', {})
            digest = source_digest(source.get('sha256'), {url: v.get('sha256') for url, v in supporting.items()})
            if not readable or digest != p['sourceSha256'] or any(v.get('error') for v in supporting.values()):
                raise ValueError('Calculation audit source differs from the current evidence')
            start = datetime.fromisoformat(calc['validFrom']) if calc['validFrom'] else None
            end = datetime.fromisoformat(calc['validUntil'])
            if (start and generated < start) or generated > end:
                raise ValueError('Calculation is outside its current valid period')
        # A formula's existence never verifies an individual's current facts.
        kind = ('conditionalAmount' if calc and calc['rounding'] != 'notModeled'
                else 'nominalRateOnly' if calc else 'notModeled')
        required = set(calc['requiredConditionIds']) if calc else set()
        conditions = [dict(**c, responsibility=RESPONSIBILITY[c['evidence']],
                           requiredForCurrentFormula=c['id'] in required,
                           status='requiresCurrentEvidence') for c in p['conditions']]
        blockers = list(p['unmodeledBenefits'])
        if not readable:
            blockers.insert(0, '官方公告無法完整讀取，需修復來源後重新核對')
        if not calc:
            blockers.insert(0, '尚無經完整來源核准的可用計算公式')
        elif kind == 'nominalRateOnly':
            blockers.insert(0, '取整或結算單位未確認，不可產生回饋金額')
        entries.append(dict(cardId=cid, modelStatus=kind,
                            sourceReadable=readable, sourceUrl=source.get('url'),
                            sourceSha256=source.get('sha256'), sourceError=source.get('error'),
                            formulaSourceSha256=p['sourceSha256'] if calc else None,
                            calculation=calc, conditions=conditions,
                            unresolvedRuleGaps=blockers,
                            transactionEligibilityVerified=False,
                            fullCardCoverage=False))
    from wallet_campaigns import validate_wallet_campaign
    campaigns = []
    for c in feed.get('walletCampaigns', []):
        validate_wallet_campaign(c)
        campaigns.append(dict(campaignId=c['campaignId'], sourceId=c['sourceId'],
                              sourceSha256=c['sourceSha256'], sourceUrl=c['officialUrl'],
                              transactionEligibilityVerified=False,
                              requirements=[dict(**r, status='requiresCurrentEvidence',
                                  responsibility=('平台確認剩餘名額' if r['id'] in {'poolAvailable','first830Approved'}
                                                  else '使用者提供當期有效資料與交易紀錄'))
                                            for r in c['requirements']],
                              fundingTypes=c['fundingTypes'], fundingBankId=c['fundingBankId'],
                              rewardAmount=c['rewardAmount'], rewardUnit=c['rewardUnit'],
                              grantScope=c['grantScope'], requiredTransactions=c['requiredTransactions'],
                              stacking=c['stacking']))
    return dict(schemaVersion=1, generatedAt=feed['generatedAt'], expiresAt=feed['expiresAt'],
                sourceCount=len(entries), fullyVerifiedEntitlementCount=0,
                conditionalAmountCount=sum(e['modelStatus']=='conditionalAmount' for e in entries),
                nominalRateOnlyCount=sum(e['modelStatus']=='nominalRateOnly' for e in entries),
                notModeledCount=sum(e['modelStatus']=='notModeled' for e in entries),
                sources=entries, walletCampaignCount=len(campaigns),
                walletCampaigns=campaigns, walletCampaignPending=feed.get('walletCampaignPending', {}))
