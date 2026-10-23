"""Condition inventory, deliberately separate from full-card eligibility claims."""
import json
import hashlib
from pathlib import Path
from datetime import datetime
import re

APPROVALS = json.loads(Path(__file__).with_name('calculation_approvals.json').read_text())
SUPPORT_APPROVALS = json.loads(Path(__file__).with_name('calculation_support_approvals.json').read_text())

def source_digest(primary, supporting):
    if not primary: return None
    if not supporting: return primary
    return hashlib.sha256(json.dumps({'primary':primary,'supporting':supporting},sort_keys=True,separators=(',',':')).encode()).hexdigest()

POLICIES = {
 'cathay_cube': ('小樹點(信用卡)','halfUp','2026-08-03T00:00:00+08:00',['fixedScheme','domesticTwd','nonInsurance','withinCubeMonthlyLimit']),
 'ctbc_linepay': ('LINE POINTS','halfUp','2026-07-01T00:00:00+08:00',['lineBinding']),
 'ctbc_linepay_debit': ('LINE POINTS','halfUp','2026-07-01T00:00:00+08:00',['lineBinding']),
 'taishin_gogo': ('台新 Point','floor','2026-07-01T00:00:00+08:00',['cardMigration']),
 'jkopay_card': ('街口幣','halfUp','2026-01-01T00:00:00+08:00',['jkoBinding','restrictedStores']),
 'esun_ubear': ('元現金回饋','floor','2026-09-01T00:00:00+08:00',['ebill','autopay']),
 'esun_unicard': ('玉山 e point','halfUp','2026-07-01T00:00:00+08:00',['ebill','autopay']),
 'federal_jihe': ('名目回饋率','notModeled','2026-07-01T00:00:00+08:00',['domesticTwd']),
 'taishin_richart_debit': ('名目回饋率','notModeled','2026-07-01T00:00:00+08:00',['mastercardDebit','debitPosting']),
 'fubon_costco': ('好多金','halfUp',None,['outsideCostco','costcoMembership','postingDeadline']),
 'fubon_j': ('LINE POINTS','floor','2026-04-01T00:00:00+08:00',['cardVariant','ebillAutopay','stableRewardEligibility','lineBinding','domesticCharge']),
 'sinopac_sport': ('名目回饋率','notModeled','2026-07-01T00:00:00+08:00',['ebill','dacardLinked']),
}

KINDS = {'cathay_cube':'cubeFixedDomesticPlan','esun_ubear':'ubearGeneralPlan','esun_unicard':'unicardGeneralPlan',
 'federal_jihe':'nominalFederalDomestic','taishin_richart_debit':'nominalRichartDebit',
 'fubon_costco':'costcoOutsidePlan','fubon_j':'jPointsGeneralPlan','sinopac_sport':'nominalSportGeneral'}

CATEGORIES = {'transaction', 'payment', 'membership', 'cycle', 'quota', 'calculation', 'issuerDecision'}
COMMON = [
 ('cardActive','membership','卡片或帳戶於交易與回饋時仍有效','userProfile'),
 ('accountGoodStanding','membership','帳戶狀態及繳款符合計畫，沒有因逾期、未繳足最低應繳款、停權或違約而喪失資格','userProfile'),
 ('paymentRoute','payment','此商家與付款資金來源確實支援所選支付路徑','transaction'),
 ('generalSpend','issuerDecision','本筆符合此基本方案的一般消費認列及完整排除清單','issuerDecision'),
 ('nonInstallment','transaction','本筆非分期、非自動加值及非預借現金','transaction'),
 ('netTwdCharge','transaction','金額為折抵後實際刷卡的新臺幣淨額，不含國外交易手續費等不回饋款項','transaction'),
 ('noRefundAdjustment','transaction','本次為新消費，尚無入帳後退貨、退稅、爭議或調整；本模型不處理歷史退款','transaction'),
]
SPECIFIC = {
 'ctbc_linepay': [('lineBinding','membership','交易與回饋時有效綁定正常臺灣 LINE Pay 帳號','userProfile')],
 'ctbc_linepay_debit': [('lineBinding','membership','簽帳卡與本人臺灣 LINE Pay 帳號有效連結','userProfile')],
 'taishin_gogo': [('cardMigration','membership','確認原卡已納入本期 Richart 權益；非指定切換方案加碼','userProfile')],
 'jkopay_card': [('jkoBinding','membership','交易與回饋時均有效綁定街口卡，身分資料一致','userProfile'),('restrictedStores','payment','超商或 Apple 媒體服務僅限公告指定街口付款方式','transaction')],
 'taishin_richart_debit': [('mastercardDebit','membership','持有的是 Richart Mastercard 簽帳金融卡，不是晶片金融卡扣款','userProfile'),('debitPosting','cycle','以每卡入帳日、每月結算認列，不套用青年或新戶方案','issuerDecision')],
 'esun_ubear': [('ebill','membership','電子帳單已申辦並生效','userProfile'),('autopay','membership','玉山臺幣帳戶自動扣繳已生效且成功扣款','userProfile'),('onlineCoding','issuerDecision','銀行將交易認列為網路一般消費；手機付款不代表一定屬網購','issuerDecision'),('bonusBalance','quota','本期正附卡共用網路加碼回饋尚餘多少；不是日曆月','quota'),('subscription','transaction','指定訂閱、超商、保費及其他排除項目須分開判斷','transaction')],
 'esun_unicard': [('ebill','membership','帳單 e 化已生效','userProfile'),('autopay','membership','本人玉山臺幣帳戶已成功自扣','userProfile'),('activeScheme','membership','指定加碼方案與等級已生效','userProfile'),('selectedMerchant','issuerDecision','指定通路及電子支付認列符合方案','issuerDecision')],
 'fubon_j': [('cardVariant','membership','本卡為 J Points 回饋版本；J Cash 本次尚未建模','userProfile'),('ebillAutopay','membership','回饋計算日前，已生效的本行自扣或電子帳單且取消實體帳單至少符合一項','userProfile'),('stableRewardEligibility','cycle','自消費日至入帳、回饋計算日及當期結帳日，自扣或電子帳單資格狀態均一致且持續；有申辦、取消或生效異動時不適用本模型','userProfile'),('lineBinding','membership','J Points 須本人有效臺灣 LINE Pay 綁定','userProfile'),('domesticCharge','transaction','本模型僅核對國內一般消費，未涵蓋旅遊加碼','transaction')],
 'fubon_costco': [('outsideCostco','transaction','本筆為 Costco 店外一般消費，不套用店內或加油回饋','transaction'),('excludedExamples','issuerDecision','保費、超商行動支付、全聯／大全聯、學費、稅費、醫療、代繳、小額支付平台與儲值等排除；完整清單見官方辦法','issuerDecision'),('costcoMembership','membership','卡片、會員與好多金回饋資格符合公告','userProfile'),('postingDeadline','cycle','商家在公告截止日前請款入帳','issuerDecision')],
 'federal_jihe': [('domesticTwd','transaction','屬國內一般消費且以新臺幣請款','transaction'),('japanCampaign','membership','日本、日系名店及新戶方案另需登錄／達標／限額','userProfile')],
 'sinopac_sport': [('dacardLinked','membership','已註冊 DACARD、串接運動數據且依公告開啟更新，銀行可取得本期有效運動資料；未完成時不假設可獲優惠','userProfile'),('ebill','membership','結帳前已完成有效電子或行動帳單設定','userProfile'),('sportMission','membership','運動達標、DACARD 同步與指定支付活動需另外核對','userProfile'),('bonusBalance','quota','指定支付與運動加碼的當期剩餘共用額度待確認','quota')],
 'cathay_cube': [('fixedScheme','membership','依本筆授權日與銀行紀錄，已生效且適用固定回饋方案；未確認或其他方案不適用本模型','userProfile'),('domesticTwd','transaction','本筆為臺灣商家與臺灣收單之國內新臺幣交易，不是跨境或海外實體交易','transaction'),('nonInsurance','transaction','本筆不是任何保險費交易','transaction'),('withinCubeMonthlyLimit','quota','本筆全部金額均在銀行適用之當月權益認列限額內；跨越上限或剩餘額度不明時不適用','quota'),('activeScheme','membership','消費當日生效的 CUBE 方案及切換時點','userProfile'),('cubeLevel','membership','本期生效 Level 等級及升級資格','userProfile'),('selectedMerchant','issuerDecision','指定通路與第三方支付排除條款','issuerDecision')],
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
 'federal_jihe':['雖有四捨五入條款，但正附卡合併及每月入帳結算的取整單位仍待釐清；不可假設每筆或直接以整月淨額計算','日本、日系名店、新戶自扣與餐廳現折是獨立活動，尚未合併建模'],
 'taishin_richart_debit':['公告說明每卡、入帳日及每月結算，但未明列小數取整規則，僅提供名目比例','青年、新戶、指定支付加碼與入帳後退款調整另行核對'],
 'sinopac_sport':['基本 0.3% 與電子帳單加碼 0.7% 僅判斷名目比例；兩部分取整及結算方式尚待核對','DACARD、有效運動資料、運動達標、自扣、指定支付與共用額度應分別確認；運動及指定通路加碼未建模','豐點與紅利依帳單折抵設定轉換，不直接視為現金入帳'],
 'cathay_cube':['僅固定回饋之國內非保費一般消費；其他每日切換方案、等級、指定通路與優惠券尚未建模','海外實體、保費、分期、跨月額度及超額後回饋不在本模型；不得把 1.2% 套用所有交易'],
 'cathay_debit':['須先確認實際簽帳卡版本、轉換生效日與當期官方權益；不能套用 CUBE 信用卡方案','一般消費排除、點數種類、取整與有效期間尚待取得完整公告'],
 'chb_my_happy':['現有基本權益頁缺可驗證有效期間，尚不發布數值模型','數位帳戶、自扣、餘額、每月登錄、六大通路認列及正附卡共用上限須逐項核對'],
 'jko_richart_account':['新舊戶、開通日、資料共享授權與存款任務會影響次期權益，尚未取得完整可監測條款','次月 5 日至次次月 4 日的回饋區間、會員等級及使用額度須另外建立模型'],
 'pxplus_account':['目前來源僅代表特定活動，不是全支付所有交易的通用回饋','需逐活動取得消費期間、合格通路、銀行帳戶／錢包／綁卡資金來源、任務、個人及總量上限與取整'],
 'taiwanpay_bonus':['台灣 Pay 活動入口不等於單一銀行的回饋方案，需先選定發卡銀行及活動','金融卡／帳戶／信用卡與 TWQR 路徑、商家、登錄、達檻及活動總量均須分開核對'],
 'pluspay_account':['尚未取得可完整解析的當期活動辦法；錢包與綁卡優惠不可互套','指定通路、消費期間、登錄、每戶／每月上限及額滿公告須逐活動核對'],
 'easywallet_account':['單一日常採買活動的額滿狀態只適用該活動；不得推論所有悠遊付活動均停止','其他活動需分開核對會員任務、付款資金來源、回饋券、個人上限及總量'],
 'ipass_money_account':['優惠列表未形成個別活動公式；舊一卡通 MONEY 與當期 iPASS MONEY 活動不可直接沿用','需取得個別活動期間、通路、付款路徑、會員任務、登錄及個人／總量額滿狀態'],
 'fubon_j':['J Cash 版本、海外旅遊、新戶、保費及其他加碼尚未完整建模','公告對資格生效時點有不同敘述；僅支援消費至結算期間資格狀態一致的 J Points 國內非分期消費，異動情境待銀行釐清'],
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
        supporting = {url: value.get('sha256') for url,value in source.get('supportingSources', {}).items()}
        expected_support = SUPPORT_APPROVALS.get(cid, {})
        digest = source_digest(source.get('sha256'), supporting)
        calculation = None
        if rule and cid in POLICIES and source.get('sha256') == APPROVALS.get(cid) and supporting == expected_support:
            unit, rounding, start, extra = POLICIES[cid]
            kind = KINDS.get(cid,'flatGeneralPlan')
            calculation = {'kind': kind, 'rate': 1.0 if cid in {'esun_ubear','esun_unicard'} else rule['promoRewardRate'],
                'unit': unit, 'rounding': rounding, 'validFrom': start, 'validUntil': rule['validUntil'],
                'requiredConditionIds': [i[0] for i in COMMON] + extra}
        result[cid] = {
            'version': 1, 'cardId': cid, 'scope': 'limitedGeneralPlan' if rule else 'notModeled',
            'announcementObserved': rule is not None,
            'sourceSha256': digest,
            'officialUrl': rule['officialUrl'] if rule else source.get('url'),
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
        if cid not in POLICIES or not profile['announcementObserved'] or not digest or digest != source_digest(APPROVALS.get(cid), SUPPORT_APPROVALS.get(cid, {})):
            raise ValueError('Unapproved calculation evidence')
        if not isinstance(calc,dict) or set(calc) != {'kind','rate','unit','rounding','validFrom','validUntil','requiredConditionIds'}:
            raise ValueError('Invalid flat calculation schema')
        unit, rounding, start, extra = POLICIES[cid]
        expected_kind = KINDS.get(cid,'flatGeneralPlan')
        if calc['kind'] != expected_kind or calc['unit'] != unit or calc['rounding'] != rounding or calc['validFrom'] != start:
            raise ValueError('Unsupported flat calculation policy')
        if cid in {'esun_ubear','esun_unicard'} and calc['rate'] != 1:
            raise ValueError('Unsupported qualified general rate')
        from validator import rate, date
        if not rate(calc['rate']) or (calc['validFrom'] is not None and date(calc['validFrom']) >= date(calc['validUntil'])):
            raise ValueError('Invalid flat calculation rate or period')
        if calc['requiredConditionIds'] != [i[0] for i in COMMON] + extra:
            raise ValueError('Incomplete flat calculation conditions')
