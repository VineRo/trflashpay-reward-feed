"""Scoped platform/bank campaigns: never universal wallet base rates."""
import json
import re
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
APPROVALS = json.loads((ROOT/'wallet_campaign_approvals.json').read_text())
SOURCES = json.loads((ROOT/'wallet_sources.json').read_text())
URLS = {s['cardId']:s['url'] for s in SOURCES}
COMMON = [
 ('memberActive','支付會員、付款工具及回饋帳戶於交易與發放時均有效，無違約、停權或儲值超限'),
 ('routeSupported','商家與支付 App 確實支援本次選擇的扣款工具，並已完成必要綁定'),
 ('eligibleNetSpend','輸入的是折抵後實付金額，已扣除菸品等依法不可促銷商品及完整活動排除項目'),
 ('noRefund','本次為新交易，無退款、取消、爭議或已入帳後調整'),
 ('unusedGrant','本活動在適用期間／月份尚未領取，且本次不是重複申請或已發放交易'),
 ('poolAvailable','已在支付 App／銀行確認本次仍符合限量名額；網頁未寫額滿不代表仍有名額'),
]

def entry(cid, source, platform, name, funding, bank, minimum, award, unit, start, end, scope, specific, *, count=1, note=''):
 return dict(version=1,campaignId=cid,sourceId=source,platform=platform,name=name,
  fundingTypes=funding,fundingBankId=bank,kind='taskCompletion' if count>1 else 'fixedThreshold',
  minimumAmount=minimum,rewardAmount=award,rewardUnit=unit,requiredTransactions=count,
  validFrom=start,validUntil=end,grantScope=scope,
  requirements=[dict(id=k,label=v) for k,v in COMMON+specific],
  scopeDescription=note,stacking='separateOnly',availability='requiresConfirmation')

DEFINITIONS = [
 entry('px_income_binding_2026','wallet_px_income','pxPlus','全支付：收益進帳綁定消費任務',
  ['creditCard','bankAccount','storedBalance'],None,200,200,'全點',
  '2026-08-01T00:00:00+08:00','2026-10-31T23:59:59.999999+08:00','calendarMonthly',[
  ('existingDirectClient','2026/6/30 前已是摩根 DIRECT 客戶，且符合本綁定系列的首次參加資格'),
  ('priorMonthBinding','本次消費月份前月的指定檢核日（7/14、8/14 或 9/14）前綁定成功並維持境內基金配息入帳全支付')],
  note='單筆滿 200 元贈 200 全點，每戶每月一次，三個月合計上限 600；此為任務贈點，不是固定 100% 回饋。不含基金投資建議或投資報酬。'),
 entry('px_income_dividend_2026','wallet_px_income','pxPlus','全支付：前月配息入帳消費任務',
  ['creditCard','bankAccount','storedBalance'],None,200,200,'全點',
  '2026-08-01T00:00:00+08:00','2026-10-31T23:59:59.999999+08:00','calendarMonthly',[
  ('existingDirectClient','2026/6/30 前已是摩根 DIRECT 客戶，且持有符合活動的境內配息型基金'),
  ('priorMonthDividend','本次消費前一個月（2026/7、8 或 9 月）已有符合條件的單檔境內基金配息成功入帳全支付，並維持綁定')],
  note='單筆實付滿 200 元贈 200 全點，每戶每月一次，三個月合計上限 600；本次只顯示此任務，不自動加上綁定任務或銀行回饋。'),
 entry('easy_yuanta_tuition_2026','wallet_easy_yuanta','easyWallet','悠遊付：元大帳戶直扣學雜費',
  ['bankAccount'],'yuanta',5999,100,'元悠遊付回饋金',
  '2026-09-01T00:00:00+08:00','2026-10-31T23:59:59.999999+08:00','totalCampaign',[
  ('tuitionPayment','本筆為公告認列的學雜費，直接從本人元大新臺幣活期存款帳戶成功扣款')],
  note='單筆滿 5,999 元贈 100 元，每個悠遊付帳戶活動期間一次、總限額 1,000 名。元大卡號刷卡或先儲值再付款均不適用此直扣模型；其他月級、週五及回饋券不自動相加。'),
 entry('ipass_obank_new_debit_2026','wallet_ipass_obank','ipassMoney','iPASS MONEY：王道新戶簽帳卡三筆任務',
  ['debitCard'],'obank',0.01,600,'元 iPASS MONEY 儲值金',
  '2026-03-01T00:00:00+08:00','2026-12-30T23:59:59+08:00','totalCampaign',[
  ('newAccountViaLink','透過 iPASS MONEY 指定連結首次開立王道帳戶，從未持有王道任何存款帳戶'),
  ('withinThirtyDays','本筆與前述合格筆數均在核卡後 30 天內，且符合銀行認列交易期間'),
  ('ordinaryNonConvenience','本筆與計入筆數的消費均符合王道一般消費與全部排除條款，不含四大超商'),
  ('first830Approved','已向王道銀行確認屬本活動前 830 名合格核卡客戶，不能只以頁面未公告額滿推定')],count=3,
  note='僅在本次完成第 3 筆合格交易時試算一次性 600 元任務獎勵，不是每筆 600 元，也不是銀行帳戶直扣或儲值交易。388 元加碼、存款利息與卡片基本回饋不自動相加。'),
]


def build_wallet_campaigns(evidence, now=None):
 now=now or datetime.now(timezone.utc)
 accepted,pending=[],{}
 for definition in DEFINITIONS:
  cid,sid=definition['campaignId'],definition['sourceId'];source=evidence.get(sid,{})
  if source.get('error') or not source.get('text'):
   pending[cid]='官方活動條款目前無法完整讀取';continue
  if source.get('url')!=URLS[sid] or source.get('sha256')!=APPROVALS.get(sid):
   pending[cid]='官方活動公告已變更，公式暫停，待重新核對';continue
  text=re.sub(r'\s+','',source['text'])
  if re.search(r'本活動(?:現已|已經|已)額滿|【已額滿】|\[已額滿\]',text):
   pending[cid]='官方公告活動已額滿，停止試算';continue
  if not datetime.fromisoformat(definition['validFrom'])<=now<=datetime.fromisoformat(definition['validUntil']):
   pending[cid]='不在本活動可試算的交易期間';continue
  campaign={**definition,'officialUrl':source['url'],'sourceSha256':source['sha256']}
  validate_wallet_campaign(campaign)
  accepted.append(campaign)
 return accepted,pending


def validate_wallet_campaign(campaign):
 from urllib.parse import urlparse
 if not isinstance(campaign,dict): raise ValueError('Campaign must be an object')
 definitions={d['campaignId']:d for d in DEFINITIONS}
 base=definitions.get(campaign.get('campaignId'))
 if base is None or set(campaign)!=set(base)|{'officialUrl','sourceSha256'}:
  raise ValueError('Unsupported campaign schema')
 if any(campaign[k]!=v for k,v in base.items()): raise ValueError('Unreviewed campaign contract')
 sid=campaign['sourceId']
 if campaign['officialUrl']!=URLS[sid] or campaign['sourceSha256']!=APPROVALS[sid]:
  raise ValueError('Unapproved campaign source')
 if not re.fullmatch('[0-9a-f]{64}',campaign['sourceSha256']): raise ValueError('Invalid source hash')
 u=urlparse(campaign['officialUrl'])
 if u.scheme!='https' or not u.hostname or u.username or u.password: raise ValueError('Invalid source URL')
 for k in ['minimumAmount','rewardAmount']:
  v=campaign[k]
  if type(v) not in (int,float) or not 0<v<=1000000000: raise ValueError('Invalid amount')
 if campaign['rewardAmount']%1 or campaign['rewardAmount']>100000: raise ValueError('Invalid fixed award')
 count=campaign['requiredTransactions']
 if type(count) is not int or not 1<=count<=100 or campaign['kind']!=('fixedThreshold' if count==1 else 'taskCompletion'):
  raise ValueError('Invalid task count')
 if not campaign['fundingTypes'] or len(set(campaign['fundingTypes']))!=len(campaign['fundingTypes']) or any(v not in {'creditCard','debitCard','bankAccount','storedBalance'} for v in campaign['fundingTypes']):
  raise ValueError('Invalid funding routes')
 start,end=datetime.fromisoformat(campaign['validFrom']),datetime.fromisoformat(campaign['validUntil'])
 if start.tzinfo is None or end.tzinfo is None or not start<end: raise ValueError('Invalid campaign period')
 requirements=campaign['requirements']
 if not 6<=len(requirements)<=25 or len({r['id'] for r in requirements})!=len(requirements): raise ValueError('Invalid requirements')
 if not {k for k,_ in COMMON}<={r['id'] for r in requirements}: raise ValueError('Incomplete prerequisites')
 if any(not isinstance(r['label'],str) or not r['label'].strip() or len(r['label'])>400 for r in requirements): raise ValueError('Invalid condition text')
