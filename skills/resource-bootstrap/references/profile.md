# Profile 與 CLI

在 SKILL.md 步驟 2 讀本文件。設定使用 JSON，避免 YAML parser 的額外相依。

## 檔案責任

| 檔案 | 用途 |
| --- | --- |
| `.agent/resources.json` | 可分享的資源端點、來源證據與映射；只存 credential reference |
| `.agent/resource-plan.json` | 本次任務的必要／選用資源與理由；任務改變時重新核對 |
| `.agent/resources.local.json` | 可選的完整本機 profile；應先加入 repo 的 ignore 規則再保存 |

CLI v1 不隱式合併 profile。若使用 local profile，先處理它與 repo 設定的衝突，
再明確指定 `--profile .agent/resources.local.json`。它必須是一份完整且經確認的設定。
CI 需要可取得的等價配置；不要依賴只存在開發機的 local 檔案。
已有 YAML 或其他 profile 時，保留原有用途，僅將本次相關、去除秘密的欄位轉成 CLI 支持的 JSON。
不要安裝 YAML 函式庫，也不要用簡易文字切割器假裝完整解析 YAML。

## 建立設定

讀 [resources.example.json](../assets/resources.example.json) 與 [plan.example.json](../assets/plan.example.json)，
使用 agent 的編輯工具填入真實值。範例中的 host、project、evidence 不代表任何現成可用資源。
CLI 不會自動保存設定或替使用者批准端點。

Profile v1 包含五個必填頂層欄位：

| 欄位 | 意義 |
| --- | --- |
| `schema_version` | 整數 `1` |
| `resources` | 以 resource ID 為 key 的資源物件 |
| `image_routes` | upstream registry → 已確認的 proxy project |
| `push_targets` | 名稱 → 私有 registry project，與 proxy 分開 |
| `direct_registries` | 明確選定、可直接使用的 image registry host 清單 |

`resources` 每項必須有 `kind`、`endpoint`、`evidence`。
kind 為 `registry`、`git`、`kubernetes`、`package-index` 或 `http`。
可選欄位為 `probe_path`、`context`、`namespace`、`credentials`。
`evidence` 寫具體來源，例如 repo 設定位置或使用者本次提供的選擇；不要自造確認來源。

endpoint 為 HTTPS URL，不含 userinfo、query 或 fragment。
registry endpoint 只含 origin，如 `https://registry.example.internal:8443`。
其他 endpoint 可保留原有 path；`probe_path` 則明確指定從 origin 起算的絕對路徑。
例如 endpoint 是 `https://git.example.internal/gitlab`，API probe 可指定 `/gitlab/api/v4/version`。
本機診斷允許 literal loopback 的 HTTP；內網 HTTP／自訂 proxy 不屬於附件 doctor 的支持範圍。
要使用這類既有環境時，以已確認的原工具做有界限的 probe，記錄限制與結果，不修改 TLS 安全性。

credential 只允許環境變數 reference，例如：

```json
"credentials": {
  "username": {"source": "env", "name": "HARBOR_USERNAME"},
  "password": {"source": "env", "name": "HARBOR_PASSWORD"}
}
```

CLI 不解析、儲存或傳送環境變數的值，只能回報名稱是否存在。
credential helper／secret manager 等既有認證方式可在任務紀錄中引用；v1 不把它們冒充 env reference。
秘密本身不得寫入 profile、plan、log、CLI 參數或 tracked file。

## 任務需求表

Plan v1 為 `{"schema_version":1,"requirements":[...]}`。
每項包含：

| 欄位 | 意義 |
| --- | --- |
| `id` | 本次 plan 內不重複的 ID |
| `required` | boolean；是否為這次任務的必要資源 |
| `kind` | `resource`、`image` 或 `push` |
| `value` | 分別是 resource ID、原始 image reference 或 push target 名稱 |
| `reason` | 對應到本次動作的需求原因 |
| `source` | 使用者計畫或 repo 檔案與位置 |

空 requirements 適用於本次沒有外部資源的工作。必要性必須由任務推導，不以刪除需求讓 check 變綠。
像 context、namespace、runner 能力與 secret 機制這些語意要求，仍要由 agent 按任務核對；
CLI 的 resource 存在檢查不代表它們已滿足。

## 命令

`--root`、`--profile`、`--plan` 放在 subcommand 前。
下面的 `<cli>` 是 skill 的 `scripts/resource.py`，或複製到 repo 後的 `tools/resource.py`。

```sh
python3 <cli> --root <repo> analyze
python3 <cli> --root <repo> list
python3 <cli> --root <repo> resolve --image golang:1.25
python3 <cli> --root <repo> resolve --push-target application --image api:1.0
python3 <cli> --root <repo> check
python3 <cli> --root <repo> check --dockerfiles
python3 <cli> --root <repo> doctor --resource harbor
```

analyze 不需要 profile 或 plan。list／resolve／doctor 使用 profile；check 另使用 plan。
check 驗證設定可解析；doctor 回報特定端點的一次 probe，兩者不互相取代。
`check --dockerfiles` 還會驗證實際 Dockerfile 的 FROM 是否已使用 resolved reference。

所有命令輸出 JSON，包含 `schema_version`、`status` 與 `findings`。
每筆 finding 包含 `rule_id`、`source`、`violation`、`expected`、`suggested_fix`。

- exit `0`：該命令檢查的範圍通過。
- exit `1`：仍有必要設定缺口、未完成驗證或檢查失敗。
- exit `2`：設定格式或工具執行錯誤。

保留檢查的範圍與結果一起交付。例如 `check` exit 0 只能稱為「設定檢查通過」，不能稱為「部署就緒」。
