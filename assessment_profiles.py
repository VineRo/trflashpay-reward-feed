"""Condition inventory, deliberately separate from full-card eligibility claims."""
import json
from pathlib import Path
from datetime import datetime
import re

APPROVALS = json.loads(Path(__file__).with_name('calculation_approvals.json').read_text())
POLICIES = {
 'ctbc_linepay': ('LINE POINTS','halfUp','2026-07-01T00:00:00+08:00',['lineBinding']),
 'ctbc_linepay_debit': ('LINE POINTS','halfUp','2026-07-01T00:00:00+08:00',['lineBinding']),
 'taishin_gogo': ('台新 Point','floor','2026-07-01T00:00:00+08:00',['cardMigration']),
 'jkopay_card': ('街口幣','halfUp','2026-01-01T00:00:00+08:00',['jkoBinding','restrictedStores']),
 'esun_ubear': ('元現金回饋','floor','2026-09-01T00:00:00+08:00',['ebill','autopay']),
 'esun_unicard': ('玉山 e point','halfUp','2026-07-01T00:00:00+08:00',['ebill','autopay']),
}

CATEGORIES = {'transaction', 'payment', 'membership', 'cycle', 'quota', 'calculation', 'issuerDecision'}
COMMON = [
 ('cardActive','membership','卡片或帳戶於交易與回饋時仍有效','userProfile'),
 ('paymentRoute','payment','此商家與付款資金來源確實支援所選支付路徑','transaction'),
 ('generalSpend','issuerDecision','本筆依銀行認列為一般消費且不在完整排除清單內','issuerDecision'),
 ('nonInstallment','transaction','本筆非分期、非自動加值及非預借現金','transaction'),
 ('netTwdCharge','transaction','金額為折抵與退款後實際刷卡的新臺幣淨額','transaction'),
]
SPECIFIC = {
 'ctbc_linepay': [('lineBinding','membership','交易與回饋時有效綁定正常臺灣 LINE Pay 帳號','userProfile')],
 'ctbc_linepay_debit': [('lineBinding','membership','簽帳卡與本人臺灣 LINE Pay 帳號有效連結','userProfile')],
 'taishin_gogo': [('cardMigration','membership','確認原卡已納入本期 Richart 權益；非指定切換方案加碼','userProfile')],
 'jkopay_card': [('jkoBinding','membership','交易與回饋時均有效綁定街口卡，身分資料一致','userProfile'),('restrictedStores','payment','超商或 Apple 媒體服務僅限公告指定街口付款方式','transaction')],
 'taishin_richart_debit': [('mastercardDebit','membership','持有的是 Richart Mastercard 簽帳金融卡，不是晶片金融卡扣款','userProfile'),('debitPosting','cycle','以每卡入帳日、每月結算認列，不套用青年或新戶方案','issuerDecision')],
 'esun_ubear': [('ebill','membership','電子帳單已申辦並生效','userProfile'),('autopay','membership','玉山臺幣帳戶自動扣繳已生效且成功扣款','userProfile'),('onlineCoding','issuerDecision','銀行將交易認列為網路一般消費；手機付款不代表一定屬網購','issuerDecision'),('bonusBalance','quota','本期正附卡共用網路加碼回饋尚餘多少；不是日曆月','quota'),('subscription','transaction','指定訂閱、超商、保費及其他排除項目須分開判斷','transaction')],
 'esun_unicard': [('ebill','membership','帳單 e 化已生效','userProfile'),('autopay','membership','本人玉山臺幣帳戶已成功自扣','userProfile'),('activeScheme','membership','指定加碼方案與等級已生效','userProfile'),('selectedMerchant','issuerDecision','指定通路及電子支付認列符合方案','issuerDecision')],
 'fubon_j': [('cardVariant','membership','區分 J Cash 與 J Points 回饋版本','userProfile'),('ebillAutopay','membership','電子帳單／自動扣繳及權益生效時間符合該版本','userProfile'),('lineBinding','membership','J Points 須本人有效臺灣 LINE Pay 綁定','userProfile'),('domesticCharge','transaction','本模型僅核對國內一般消費，未涵蓋旅遊加碼','transaction')],
 'fubon_costco': [('outsideCostco','transaction','本筆為 Costco 店外一般消費，不套用店內或加油回饋','transaction'),('costcoMembership','membership','卡片、會員與好多金回饋資格符合公告','userProfile'),('postingDeadline','cycle','商家在公告截止日前請款入帳','issuerDecision')],
 'federal_jihe': [('domesticTwd','transaction','屬國內一般消費且以新臺幣請款','transaction'),('japanCampaign','membership','日本、日系名店及新戶方案另需登錄／達標／限額','userProfile')],
 'sinopac_sport': [('ebill','membership','結帳前已完成有效電子或行動帳單設定','userProfile'),('sportMission','membership','運動達標、DACARD 同步與指定支付活動需另外核對','userProfile'),('bonusBalance','quota','指定支付與運動加碼的當期剩餘共用額度待確認','quota')],
 'cathay_cube': [('activeScheme','membership','消費當日生效的 CUBE 方案及切換時點','userProfile'),('cubeLevel','membership','本期生效 Level 等級及升級資格','userProfile'),('selectedMerchant','issuerDecision','指定通路與第三方支付排除條款','issuerDecision')],
 'cathay_debit': [('debitVariant','membership','區分 CUBE／KOKO／其他簽帳卡與轉換日期','userProfile')],
 'chb_my_happy': [('chbAccount','membership','數位帳戶、自扣、平均餘額與生效帳單期','userProfile'),('sixChannels','issuerDecision','六大通路 MCC 與非指定支付排除條款','issuerDecision'),('bonusBalance','quota','當期加碼上限與共用正附卡額度','quota')],
 'jko_richart_account': [('openingDate','membership','街利存開通日對應的新舊戶方案','userProfile'),('depositMission','membership','已完成指定營業日餘額任務及有效資料共享授權','userProfile'),('jkoTier','membership','回饋當下街口會員等級','userProfile'),('rewardWindow','cycle','次月5日至次次月4日的回饋期間','userProfile'),('bonusBalance','quota','本回饋期間已用共用額度','quota')],
 'pxplus_account': [('fundingSource','payment','區分全支付錢包、銀行帳戶扣款與信用卡','transaction'),('campaignTask','membership','通路活動、新戶或其他指定任務是否達成','userProfile'),('campaignPool','quota','活動總量與個人回饋額度是否仍可用','quota')],
 'taiwanpay_bonus': [('issuerAndRoute','payment','確認付款銀行、金融卡／帳戶／信用卡及 TWQR 路徑','transaction'),('campaignMerchant','issuerDecision','實際商家是否屬該銀行當期指定活動','issuerDecision'),('registration','membership','活動登錄與達檻狀態','userProfile'),('campaignPool','quota','個人與活動總量剩餘額度','quota')],
 'pluspay_account': [('fundingSource','payment','區分錢包與綁卡，以及指定通路','transaction'),('campaignPool','quota','每戶、每月與活動限額／登錄狀態','quota')],
 'easywallet_account': [('fundingSource','payment','區分悠遊付錢包、連結銀行帳戶與信用卡','transaction'),('memberMission','membership','會員月級任務及回饋券適用條件','userProfile'),('campaignPool','quota','指定活動是否額滿；不將單一額滿活動套用全部活動','quota')],
 'ipass_money_account': [('fundingSource','payment','區分錢包、銀行扣款、綁卡及付款碼／掃碼通路','transaction'),('campaignTask','membership','指定活動、新戶或登錄資格','userProfile'),('campaignPool','quota','個人額度、總量額滿及公告停止時間','quota')],
}
UNMODELED = {
 'esun_ubear':['網路消費額外加碼、每期共用上限及指定訂閱獨立回饋；本次僅計一般基本回饋'],
 'esun_unicard':['簡單選、任意選、UP選與百大通路加碼及各月共用上限'],
 'ctbc_linepay':['海外實體、特店加碼與各店獨立條件'],
 'ctbc_linepay_debit':['特店加碼；不得套用信用卡海外實體加碼'],
 'taishin_gogo':['切換方案、保費、指定通路、新戶及領券加碼'],
 'jkopay_card':['精選通路、繳費、首刷、新戶及限量領券加碼'],
}


def build_profiles(evidence, rules, coverage):
    byid = {r['cardId']: r for r in rules}
    result = {}
    for cid in coverage:
        source, rule = evidence.get(cid, {}), byid.get(cid)
        items = COMMON + SPECIFIC.get(cid, [])
        calculation = None
        if rule and cid in POLICIES and source.get('sha256') == APPROVALS.get(cid):
            unit, rounding, start, extra = POLICIES[cid]
            kind = {'esun_ubear':'ubearGeneralPlan','esun_unicard':'unicardGeneralPlan'}.get(cid,'flatGeneralPlan')
            calculation = {'kind': kind, 'rate': 1.0 if cid in {'esun_ubear','esun_unicard'} else rule['promoRewardRate'],
                'unit': unit, 'rounding': rounding, 'validFrom': start, 'validUntil': rule['validUntil'],
                'requiredConditionIds': [i[0] for i in COMMON] + extra}
        result[cid] = {
            'version': 1, 'cardId': cid, 'scope': 'limitedGeneralPlan' if rule else 'notModeled',
            'announcementObserved': rule is not None,
            'sourceSha256': source.get('sha256'),
            'officialUrl': source.get('url'),
            'conditions': [dict(id=i, category=c, label=l, evidence=e) for i,c,l,e in items],
            'unmodeledBenefits': UNMODELED.get(cid, ['尚未完成所有通路、方案、資格、上限與取整組合建模']),
            'fullCardCoverage': False, 'calculation': calculation,
        }
    return result


def validate_profile(profile, cid):
    if not isinstance(profile, dict) or set(profile) != {'version','cardId','scope','announcementObserved','sourceSha256','officialUrl','conditions','unmodeledBenefits','fullCardCoverage','calculation'}:
        raise ValueError('Invalid assessment profile schema')
    if type(profile['version']) is not int or profile['version'] != 1 or profile['cardId'] != cid or profile['fullCardCoverage'] is not False:
        raise ValueError('Unsupported assessment claim')
    if profile['scope'] not in {'limitedGeneralPlan','notModeled'} or type(profile['announcementObserved']) is not bool:
        raise ValueError('Invalid assessment scope')
    if profile['announcementObserved'] != (profile['scope'] == 'limitedGeneralPlan'):
        raise ValueError('Inconsistent observation status')
    digest = profile['sourceSha256']
    if digest is not None and (not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)):
        raise ValueError('Invalid source digest')
    items=profile['conditions']
    if not isinstance(items,list) or not 1 <= len(items) <= 30 or len({i.get('id') for i in items if isinstance(i,dict)}) != len(items):
        raise ValueError('Invalid condition inventory')
    for item in items:
        if not isinstance(item,dict) or set(item) != {'id','category','label','evidence'} or item['category'] not in CATEGORIES or item['evidence'] not in {'userProfile','transaction','issuerDecision','quota'} or any(not isinstance(item[k],str) or not item[k].strip() or len(item[k])>300 for k in item):
            raise ValueError('Invalid condition entry')
    if not isinstance(profile['unmodeledBenefits'],list) or not profile['unmodeledBenefits'] or any(not isinstance(v,str) or not v.strip() or len(v)>300 for v in profile['unmodeledBenefits']):
        raise ValueError('Missing unmodeled scope')

    calc = profile['calculation']
    if calc is not None:
        if cid not in POLICIES or not profile['announcementObserved'] or not digest or digest != APPROVALS.get(cid):
            raise ValueError('Unapproved calculation evidence')
        if not isinstance(calc,dict) or set(calc) != {'kind','rate','unit','rounding','validFrom','validUntil','requiredConditionIds'}:
            raise ValueError('Invalid flat calculation schema')
        unit, rounding, start, extra = POLICIES[cid]
        expected_kind = {'esun_ubear':'ubearGeneralPlan','esun_unicard':'unicardGeneralPlan'}.get(cid,'flatGeneralPlan')
        if calc['kind'] != expected_kind or calc['unit'] != unit or calc['rounding'] != rounding or calc['validFrom'] != start:
            raise ValueError('Unsupported flat calculation policy')
        if cid in {'esun_ubear','esun_unicard'} and calc['rate'] != 1:
            raise ValueError('Unsupported qualified general rate')
        from validator import rate, date
        if not rate(calc['rate']) or date(calc['validFrom']) >= date(calc['validUntil']):
            raise ValueError('Invalid flat calculation rate or period')
        if calc['requiredConditionIds'] != [i[0] for i in COMMON] + extra:
            raise ValueError('Incomplete flat calculation conditions')
