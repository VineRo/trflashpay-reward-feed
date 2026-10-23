# TRFlashPay 銀行公告更新服務

官方來源：固定清單定時擷取。發布位置為 GitHub Pages，App 使用 HTTPS 取得有版本與有效期限的完整資料。

- 儲存庫：https://github.com/VineRo/trflashpay-reward-feed
- 優惠資料：https://vinero.github.io/trflashpay-reward-feed/rules.json
- 來源狀態：https://vinero.github.io/trflashpay-reward-feed/status.json
- 每日台北時間 09:17 執行，GitHub 排程可能延遲。

## 正確性邊界

`catalog.json` 列出 App 的 20 個內建付款來源。未核對的卡片會明確列為 pending，App 不會使用其內建範例回饋率當作銀行當期優惠。新資料有效期 36 小時；快取過期時也回到待核對狀態。使用者獨立 ID 的自訂卡片仍保留自訂規則。

目前五個公告來源：玉山 U Bear、中信 LINE Pay、富邦 J、國泰 CUBE、台新原 @GoGo。此清單並未涵蓋全部卡片。富邦頁面可能無法直接擷取可驗證條款，不能據此推定沒有回饋。

目前有兩個銀行專用解析器：

- 中信 LINE Pay：由一般消費表格與重複敘述交叉核對回饋率、有效期間及既有資格／取整條款。
- 玉山 U Bear：分別擷取基本、網路加碼、總回饋率與每期加碼上限，檢查合計一致，避免誤取新戶或數位訂閱的廣告最高數字。

這些數字與上限可隨符合既有語意格式的官方公告自動更新；格式缺漏、數字互相矛盾或有效期不符時回到 pending。兩者目前皆保留 `verificationRequiredReason`：實際一般消費認列、會員資格、排除交易及剩餘額度尚未完整判定，App 不得直接將公告數字當作此筆交易的確定回饋。

**已自動化的是上述兩家指定條款數字的擷取、公告檢查、撤回無法驗證的資料與發布。尚未完成全卡庫的交易資格判定或任意新條款的語意解析。** 其他 18 個內建來源目前仍 pending。單頁雜湊也不代表其所有連結條款均已核對。自動擷取成功不等於使用者可確定取得該回饋。

## 執行與部署

此目錄內容直接放在獨立儲存庫根目錄，包含 `.github/workflows/issuer-rules.yml`。在 Settings → Pages 選 GitHub Actions，再推送至 `main` 即執行測試、抓取、產生完整資料並部署 `public/`。工作流程只發布 JSON，不會上傳 Flutter 原始碼、交易紀錄或公告全文。公告證據保存在保留 30 天的 Actions artifact。

本機執行：

```sh
python3 -m unittest discover -s tests -v
python3 pipeline.py
python3 pipeline.py --publish
```

若環境使用 macOS 系統 CA，可設 `SSL_CERT_FILE=/etc/ssl/cert.pem`。禁止停用 TLS 驗證。網頁宣告的字元編碼會納入解碼；不會用容錯解碼掩蓋條款損壞。

App 預設已設定本儲存庫的發布網址；也可用設定檔明確指定或切換來源：

```sh
flutter build ios --dart-define-from-file=config/reward_feed.json
```

現有已安裝的 App 不會因 GitHub 部署自行取得程式變更，必須先重新建置與安裝。App 相機頁啟動約三秒後依自動更新設定下載；關閉 App 時不執行同步。

## 已核對規則

`approved.json` 的每項包含 `sourceSha256` 及完整 `rule`。官方 URL 必須完全相同，文字雜湊必須一致，規則日期必須尚未過期。完整記錄需明確包含資格、方案、特店規則、平台限制與日期欄位；明確 null 用於清除上一版本限制。

格式驗證不代表銀行條款或交易資格已驗證。無法完整表達的資格，必須提供 `verificationRequiredReason` 使 App 停止精確試算。具上限規則必須同時指定額度池、週期、上限作用範圍。不要把廣告最高百分比直接視為平台全部交易回饋。

## 待完成

- 擴充所有發卡行的來源與多條款依賴。
- 建立通路排除、會員資格、一般消費認列與各銀行取整方式的完整模型。
- 擴充其他銀行專屬解析器與交易資格模型，才可提供更多已確認試算。
- 目前以 HTTPS 固定發布來源及版本防回滾保護資料，尚無離線簽章驗證。
- 20 個來源目前多數仍待核對，這是保守防護，不能當作已完成全卡庫正確性驗收。

GitHub 官方發布流程文件：https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
