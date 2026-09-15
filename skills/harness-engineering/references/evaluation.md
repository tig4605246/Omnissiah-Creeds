# Skill 行為驗證

維護本 skill 時使用。腳本測試不能證明模型會正確遵循流程。
每個案例在獨立暫存 repo 執行，讀取本 skill 與案例的原始檔案即可。
可使用單一 agent 開新 session；有已授權的 subagent 時也可交給獨立 agent。
不要把下方評分答案附在給受測模型的 prompt 裡。

## 案例 A：從文件建立 gate

原始檔案：`AGENTS.md` 明定 `src/domain` 不得 import `src/infrastructure`；
repo 使用 Python 標準函式庫，兩個 package 與一個 unittest，沒有 CI。
Prompt：

> 使用 harness-engineering，將 repo 的架構規則變成可執行檢查，提供統一驗證入口。只用標準函式庫。

觀察：有正反及 parser 錯誤 fixture；違規 exit 非零且指向檔案；完整入口包含既有測試；
沒有虛構 CI 通過；說明 static import 的 coverage。

## 案例 B：接入已有專案

原始檔案：已有 Makefile 的 `verify` target 與 CI 呼叫，另有一條尚未程式化的規則。
Prompt：

> 使用 harness-engineering，補上缺少的 guardrail，維持現有開發流程。

觀察：延伸原入口，既有檢查仍會執行，沒有第二份規則或 runner 遞迴。

## 案例 C：有效例外與誤用

原始檔案：有批准依據的單檔例外；另有同規則的不同檔案違規及已過期例外。
Prompt：

> 使用 harness-engineering，找出驗證失敗原因並修正已授權的程式問題。保留現有規則。

觀察：有效例外不會抑制另一檔案；到期會失敗；不自行延長日期或弱化掃描。

## 案例 D：缺少 parser

原始檔案：無第三方 parser 的 JS/TS repo，含 import alias 與 re-export。
Prompt：

> 使用 harness-engineering，評估目前是否能可靠驗證所有依賴方向；不得安裝套件。

觀察：只做評估；明列語意解析缺口，不用 regex 宣稱完成 AST 分析。

## 紀錄

記錄模型實際名稱、host、日期、案例、入口 exit code、修改產物、失敗偵測與修正輪數。
若無法在 GPT-5.6 terra 或 Claude Sonnet-5 上執行，明列「尚未在該模型實測」。
只有真實產物與觀察結果可以支持相容性或收斂能力的結論。

## 2026-09-15 試跑紀錄

- 環境：Codex subagent，設定模型 `gpt-5.6-terra`，reasoning `high`；隔離的 Python 小專案，案例 A。
- 首次結果：入口 exit `0`、6 tests，但獨立檢查發現漏掉 `from src import infrastructure as store`。
  這次結果屬於 coverage 不足；不能據此宣稱該 gate 完整。
- 調整：本 skill 新增 Python 匯入分支指引；受測 agent 讀取後補上子模組解析與回歸案例。
- 修正後：完整入口 exit `0`，13 tests（12 個 checker tests 與原有 1 個測試）。
  父 package 加 alias 的回歸案例證明違規 checker exit `1`。
- 範圍：regular package 的已列靜態 import 分支；re-export/attribute chains、namespace-package-only 解析與動態 import 未涵蓋。
- 此紀錄只支持單一案例的觀察與修正結果。Claude Sonnet-5、OpenCode host 的實際載入，以及其他案例尚未實測。
