---
name: harness-engineering
description: 建立或改善 coding agent 的 verification harness；將架構與工程規範轉成可執行 guardrail、統一本機與 CI 驗證、治理例外，並依失敗回饋修正。用於 harness engineering、agentic SDLC 或將 AGENTS.md 規則程式化的任務。
---

# Harness Engineering

將專案的必要邊界轉成程式檢查，讓 agent、開發者與 CI 使用同一個入口。
完成的依據是執行結果與涵蓋範圍，不是模型宣稱「已遵守規範」。

## 執行環境

- 使用所在工具的檔案讀寫、搜尋與終端機能力。流程可由單一 agent 完成。
- 本文件的相對連結以 **skill 目錄** 為基準；專案命令在 **目標 repo** 執行。先確認這兩個位置。
- 附件使用 Python 3.9+ 標準函式庫。以 `python3` 執行；Windows 可用 `py -3`。
- 新增的輔助程式只能使用語言標準函式庫。不得依賴或執行 `pip`、`uv`、`npm`、`go get` 安裝套件。
- 可接入專案已具備的檢查工具。缺少工具時記錄為阻礙；若任務要求全部檢查也零相依，採用標準函式庫可支持的範圍，明列其餘缺口。
- JSON 設定中的命令會以目前使用者權限執行。先閱讀命令內容，確認它們屬於本次授權的驗證工作。

## 1. 判定任務與建立基線

讀取目標 repo 的 `AGENTS.md`、相關架構文件、建置設定及 CI；檢查工作目錄現有變更。
從文件與實際命令找證據，不把本 skill 的範例當成專案規則。

選擇本次路徑：

| 使用者要求 | 接下來的工作 |
| --- | --- |
| 評估、診斷、審查 harness | 只盤點、執行適用的既有檢查、報告缺口；完成後直接交付 |
| 建立、補強、修復 harness | 按步驟 2–5 實作 |
| 用既有 harness 完成已授權的程式修改 | 讀 [修正流程](references/repair-loop.md)，完成修改並用原入口驗證 |

記下：任務範圍、現有入口、必要檢查、現有失敗、執行環境與規則來源。
既有入口能安全執行時先跑一次，保留 exit code 與失敗摘要。
基線失敗仍可繼續建立 harness，但最後必須揭露尚未修復的失敗。

**完成條件：** 每項已要求的檢查都有「已有 / 缺少 / 執行受阻」狀態；任務路徑已確定。

## 2. 把意圖寫成可檢查的規則

先讀 [建立 guardrail](references/build.md)。為每條規則填寫：

```text
ID: ARCH001
來源: ARCHITECTURE.md 的依賴方向規定
範圍: src/domain 下的 Python 原始碼
禁止結果: domain 匯入 infrastructure
通過樣本: infrastructure 匯入 domain
失敗樣本: domain 匯入 infrastructure
修正方向: 將介面放在 domain/ports，由外層注入實作
Owner: 使用 repo 中已確認的維護者；未知則明列待補
```

只把明確、可重現且有來源的底線設為 gate。命名美感或抽象品質留給 review。
遇到未確定的架構方向，先完成其他有依據的規則，再詢問影響實作的選擇。
一輪先完成一條規則的完整驗證，再處理下一條；全部使用者要求都要有結果。

**完成條件：** 每條規則都有範圍、正反樣本與修正方向；推測及未涵蓋能力已標示。

## 3. 實作可執行的驗證

沿用既有入口，例如 `make verify`。只有沒有合適入口時，才採用
[runner 使用說明](references/runner.md) 與附帶模板。

每個新 gate 都必須：

1. 接受固定的輸入範圍，使用 parser 或可驗證的資料模型檢查規則。
2. 失敗時輸出 `rule_id`、`source`、`violation`、`expected`、`suggested_fix`。
3. 使用非零 exit code 回報違規或執行錯誤。設定錯誤、沒有掃到預期檔案、parser 失敗都不能算通過。
4. 以合法 fixture 證明會通過，以違規 fixture 證明會失敗，以錯誤輸入證明不會假通過。
5. 將 gate 本身的測試接到統一入口。

需要例外時才讀 [例外治理](references/exceptions.md)。例外只能豁免明確規則與檔案的 finding；
不得讓整個檢查或 parser 錯誤略過。已有授權可沿用；不得為了變綠自行放寬規則、改到期日或新增豁免。

**完成條件：** 每個新增 gate 的正例、反例及錯誤路徑都有實際執行證據。

## 4. 接入共用入口與 CI

將所有必要檢查接到同一個入口，保留原有 coverage。CI 直接呼叫該入口。
本機與 CI 使用相同設定、工具版本及必要服務；差異要有明確配置及可在本機重現的命令。
沒有 CI 平台資訊時提供可接入命令並列為缺口，先完成已知工作。

若需修改 repo 的 agent 指引，只加入入口、失敗處理方式與相關規則文件位置。
原有指引要保留；不要複製另一套規則到 agent 設定。Owner 及設定版本放在 harness 設定或既有 ownership 機制。

**完成條件：** agent、開發者與 CI 的命令指向同一組檢查；尚未執行的遠端 CI 狀態已標示。

## 5. 驗證、修正、交付

讀 [修正流程](references/repair-loop.md)。執行統一入口，根據第一個根因做最小修正，再驗證。
檢查最終 diff，確認規則、測試與例外沒有因修正而被弱化。

交付時列出：

- 入口命令與實際 exit code。
- 已程式化的規則、反例證據及必要例外。
- 尚未涵蓋的能力、既有失敗、未執行檢查與 CI 狀態。

**完成條件：** 全部必要 gate 在最後一次修改後通過；若有阻礙則回報未完成範圍與下一步，不能宣稱整體通過。

## 維護本 skill

修改附件或本流程時，執行 `python3 -I -S scripts/test_verify.py` 與
`python3 -I -S scripts/test_exceptions.py`（路徑相對 skill 目錄），再依
[行為驗證案例](references/evaluation.md) 做一次獨立試跑。
