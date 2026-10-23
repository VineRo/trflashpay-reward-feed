"""Explain unsupported sources without inventing universal reward rates."""
import re
REASONS = {
 'cathay_cube': 'CUBE 須核對當期方案與完整排除條款；固定回饋以外方案未建模，來源異動時暫停公式',
 'cathay_debit': '已取得 CUBE 簽帳金融卡當期 0.3% 公告；完整一般消費定義、簽帳卡專用取整與結算方式尚待核對，不能套用信用卡累積辦法',
 'chb_my_happy': 'My樂基本權益公告未列明有效期間；六大通路與支付排除條款尚待建模',
 'jko_richart_account': '街利存須核對開戶日、存款任務、資料授權、會員等級及跨月回饋期間',
 'pxplus_account': '全支付活動依通路、資金來源、任務與限量資格分開，尚無通用回饋規則',
 'taiwanpay_bonus': '台灣 Pay 優惠依發卡銀行、付款方式及商家活動分開，不能套用單一通用回饋率',
 'pluspay_account': '全盈+PAY 須區分錢包與綁卡、精選通路及限額，尚待建模',
 'easywallet_account': '悠遊付須區分會員任務、指定通路與總回饋額度，尚無通用回饋規則',
 'ipass_money_account': 'iPASS MONEY 須逐一核對活動與額滿公告，尚無通用回饋規則',
}
def pending_reason(cid, source):
 reason = REASONS.get(cid, '銀行當期條款尚未核對')
 if source.get('error'):
  return reason + '；官方頁面目前無法完整讀取，未採用搜尋摘要或歷史回饋率'
 text = re.sub(r'\s+', '', source.get('text', ''))
 if cid == 'easywallet_account' and '(已額滿)日常採買悠遊付' in text:
  return '官方「日常採買」活動已額滿，不能繼續引用廣告最高回饋；其他悠遊付活動須另行核對'
 return reason
