# 例外治理

只有專案確實需要例外時讀本文件並導入 helper。預設沒有例外。

## 決策與設定

先確認使用者授權或 repo 中已存在的批准依據。程式只能驗證結構與效期，不能證明 owner 已批准。
沿用已有治理格式；若沒有，採用下列 JSON 格式。日期只是範例，不能直接當成新例外的授權。

```json
{
  "version": 1,
  "exceptions": [{
    "rule": "ARCH001",
    "scope": ["src/domain/legacy.py"],
    "reason": "Migration tracked in issue 42",
    "owner": "platform",
    "expires": "2026-10-31"
  }]
}
```

helper 採取窄範圍格式：`scope` 是明列檔案的陣列，使用相對 repo 的 POSIX 路徑。
不支持目錄 prefix 或 glob；新增檔案不會自動繼承舊豁免。
需要豁免多個檔案時逐一列出。規則 ID 必須由 checker 已知規則集合提供。

`expires` 在指定日期的 UTC 當天仍有效，次日起失效。測試可注入固定日期；正式 gate 使用 UTC 時鐘。
相同程式碼、設定與評估日期得到相同結果；跨到期日改變結果是政策的一部分。
過期、重複、未知規則、空 owner/reason 或不明欄位都使整份政策驗證失敗。

## 接入 checker

讀 [exceptions.py](../assets/exceptions.py)，複製到目標 repo 的 `tools/exceptions.py`。
讓具體 checker 在同一目錄匯入它。下方為接線片段，`scan_project` 由目標 checker 實作：

```python
from exceptions import PolicyError, load_exceptions, partition_findings

# 這些 ID 由 checker 定義，不由例外檔決定。
KNOWN_RULES = {"ARCH001"}

def check_project(root):
    entries = load_exceptions(root / "harness-exceptions.json", KNOWN_RULES)
    findings = scan_project(root)  # 只回傳已成功解析的政策違規。
    active, waived = partition_findings(findings, entries)
    return active, waived
```

頂層 CLI 捕捉 `PolicyError`、parser 及 I/O 錯誤，輸出錯誤 finding 並以非零值結束。
parser、設定、runtime 錯誤直接失敗，不送進 `partition_findings`。
只要有 active finding，checker 就以非零值結束。
即使全部違規都獲批准豁免，也輸出 `waived`，保留規則、scope、owner、reason、expires 的稽核資訊。
相對路徑應由 checker 按專案 root 計算；固定掃描範圍，避免 symlink 或不同拼法使檔案身分含糊。

如需獨立驗證例外檔，可接入同一入口：

```sh
python3 tools/exceptions.py --file harness-exceptions.json --rules ARCH001
```

這條命令只驗證例外設定，不掃描程式、不套用豁免，也不能替代架構 gate。
已啟用例外機制但檔案不存在時使驗證失敗；無例外可使用 `{"version":1,"exceptions":[]}`。

## 必備測試

證明有效例外只抑制指定規則及指定檔案。
同一檔案的另一條規則、另一個檔案的同條規則，仍須失敗。
確認到期日前後、遺漏欄位、無效日期、未知 rule、過廣 scope，以及 parser 錯誤無法被豁免。
修復原違規後，移除不再需要的例外並再次執行完整入口。
