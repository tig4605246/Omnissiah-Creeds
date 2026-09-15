# 標準函式庫 runner

在專案缺少統一驗證入口時讀本文件。若已有可用入口，直接擴充既有入口。

## 放入目標專案

1. 讀 [runner 模板](../assets/verify.py) 和 [設定範例](../assets/harness.example.json)。
2. 用目前 agent 的檔案工具，將 runner 複製為目標 repo 的 `tools/verify.py`。
3. 將範例調整為 `harness.json`，填入實際 owner 與檢查命令。
4. `checks` 必須列出所有必要檢查。範例命令只示範設定結構。
5. 執行下方命令，並把相同命令接入 CI 或既有入口。

```sh
python3 tools/verify.py --config harness.json
python3 tools/verify.py --config harness.json --format json
```

runner 使用 Python 3.9+ 標準函式庫；執行前不需要安裝任何套件。
檢查命令仍需要它自己使用的工具。runner 是命令執行器，沒有內建架構分析器、安全 scanner 或測試 coverage 偵測。

## 設定介面 v1

頂層只接受 `schema_version`、`owner`、`checks`。`schema_version` 為 `1`，owner 為已確認的維護者。
`checks` 為非空陣列，每項包含：

| 欄位 | 值 |
| --- | --- |
| `id` | 不重複的穩定 check ID |
| `argv` | 非空字串陣列；第一個元素是命令，其餘是參數 |
| `source` | 負責此檢查的程式、測試或設定位置 |
| `expected` | 通過應滿足的條件 |
| `suggested_fix` | 失敗後應檢查或修正的方向 |
| `cwd` | 可省略；相對設定檔目錄的工作目錄，預設 `.` |
| `timeout_seconds` | 可省略；正的有限數值，預設 120 秒 |

命令按陣列順序逐一執行。`argv` 不經 shell 展開；`&&`、pipe、glob 和 `$VARIABLE` 不會自動處理。
一個 check 放一條實際命令。需要額外邏輯時，用標準函式庫寫成明確腳本。
禁止將命令中的任何值當成可信 sandbox：設定有執行程式的權限，執行前必須確認 scope。

空 checks、未知欄位、重複 ID 及無效設定會失敗。
runner 不知道 unittest 是否真的發現測試；測試 wrapper 應在 test count 為零時失敗。
檢查檔案範圍也由個別 gate 驗證，避免空掃描假通過。

## 結果介面 v1

JSON stdout 為單一 report，命令輸出放在 `checks[].output`，不會混入 JSON 外面。

```json
{
  "schema_version": 1,
  "status": "fail",
  "findings": [{
    "rule_id": "ARCH001",
    "source": "tools/check_architecture.py",
    "violation": "檢查以非零狀態結束；實際文字由 runner 產生",
    "expected": "domain 不依賴 infrastructure",
    "suggested_fix": "閱讀 check output 中的檔案位置並修正依賴方向"
  }],
  "checks": [{"id": "ARCH001", "status": "fail", "exit_code": 1, "output": "檢查器的詳細 finding"}]
}
```

這是欄位示意，不要求比對 `violation` 的自然語言字面值。
外層 finding 描述 check 失敗；真實違規檔案、行號與多筆 finding 由該檢查器輸出，runner 不解析或改寫。
輸出有長度上限；若被截斷，直接執行失敗 check 以取得所需細節。

- exit `0`：所有 check 通過。
- exit `1`：至少一個 check 非零，且沒有 runner 執行錯誤。
- exit `2`：設定錯誤、命令無法啟動、工作目錄錯誤或 timeout；即使其他 check 通過仍屬 error。

runner 不套用例外。需要例外的 gate 應按 [例外治理](exceptions.md) 在自己的 finding 層處理。
