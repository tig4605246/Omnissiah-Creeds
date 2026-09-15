# 建立 guardrail

在 SKILL.md 步驟 2 讀本文件。目標是把已有工程要求轉成可觀察的失敗條件。

## 分開三種責任

| 層 | 放入的內容 | 完成證據 |
| --- | --- | --- |
| 意圖 | 架構方向、使用者需求、需要人工判斷的品質 | 文件來源與明確 scope |
| 可執行政策 | 禁止的依賴方向、schema 邊界、例外效期 | 合法樣本通過，違規樣本失敗 |
| 正確性驗證 | unit、contract、integration 等專案實際需要的測試 | 測試命令結果與 coverage 範圍 |

先列出這次要求的規則，再逐條接入既有 harness。沒有來源的規則只能提出建議。
如果使用者要求涵蓋多種檢查，為每種記錄已實作或缺少的能力，不用一個綠燈代替全部能力。

## 選擇機制

| 要檢查的邊界 | 可用的標準函式庫或既有工具 | 必須確認的限制 |
| --- | --- | --- |
| Python 靜態匯入 | `ast`、`pathlib` | 相對 import、package root、alias、動態 import |
| Go 匯入方向 | `go/parser`、`go/ast`、`go/token` | build tags、產生碼、module path、測試檔 scope |
| JS/TS 依賴 | repo 已有的 parser / compiler；JS 標準 runtime 沒有完整 JS/TS parser | alias、re-export、dynamic import、tsconfig paths |
| 循環依賴 | 完整且已解析的有向圖，DFS 或 strongly connected components | unresolved edge 不能靜默略過 |
| JSON schema / API 合約 | `json` 與明確定義的相容性規則，或 repo 既有合約測試 | 比較 JSON 相等不等於 API 相容性驗證 |
| 安全政策 | 具體的可判定規則或既有 scanner | 字串搜尋不代表完成秘密偵測或漏洞掃描 |

**建立 Python import gate 時，實作前讀 [Python 匯入分支](python-imports.md)，按專案 package 結構建立該表的 fixtures。**

選 repo 已能支持的解析方式。若無法可靠分析語意，縮小已宣稱的 coverage 並揭露缺口。
文字搜尋適合初步盤點，不能宣稱它涵蓋完整語言的架構依賴。

## 實作順序

1. 明定輸入集合：root、檔案類型、排除的產生碼、build 條件。
2. 建立只含必要檔案的 fixture，放在暫存目錄或明確排除的測試資料區。
3. 寫檢查器，固定檔案與 finding 的排序。
4. 先跑合法及違規 fixture，記錄 exit code 與 finding。
5. 加上解析錯誤、空的預期 scope 及邊界案例。
6. 接到統一入口，再跑真實 repo。

違規 fixture 必須因目標規則而失敗。缺少 runtime 或語法損壞造成的非零值不是規則有效的證據。
保留 fixture 測試，避免日後重構 checker 時失去偵測能力。

## Feedback API

每個 finding 使用穩定欄位。例如：

```json
{
  "rule_id": "ARCH001",
  "source": "src/domain/order.py",
  "violation": "第 8 行匯入 src.infrastructure.database",
  "expected": "domain 只依賴 domain 與 ports",
  "suggested_fix": "在 domain 定義所需介面，將 database 實作由外層注入"
}
```

`source` 使用相對 repo 的 POSIX 檔案路徑。行號放在 `violation` 或獨立欄位，避免破壞例外的路徑比對。
修正建議描述方向；仍由 agent 讀取程式判斷具體改法。
工具錯誤要有獨立的識別碼或狀態，與業務違規分開。

## 本機與 CI

CI 呼叫既有的共用命令。若採用附件 runner，CI step 的命令可以是：

```sh
python3 tools/verify.py --config harness.json --format json
```

若已有 `make verify`，讓它呼叫上述命令；CI 仍執行 `make verify`。
不得形成 `make verify → runner → make verify` 的遞迴。
runner 本身不安裝 runtime、啟動外部服務或建立 CI pipeline。
本機及 CI 的環境準備應另有可重現步驟。缺少服務時使必要 check 失敗，回報所需條件。

## 長期維護

把 harness 程式、設定及其測試放入版本控制，使用 repo 已有的 owner 管理方式。
穩定保留 rule ID；變更輸出欄位或語意時更新介面版本與 consumer。
透過既有 CI log 或 artifact 保留結果；評估失敗能否被偵測、回饋能否促成修正、需要幾輪。
只有觀察與驗證能力足夠時，才擴大 agent 的自動操作範圍。
