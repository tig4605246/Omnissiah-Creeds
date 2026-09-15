# Omnissiag-Creeds
Machine God who rules the machines with these creeds

## Harness Engineering skill

[`skills/harness-engineering/SKILL.md`](skills/harness-engineering/SKILL.md)
將 [`harness-skill-design.md`](harness-skill-design.md) 轉成可以執行的 agent 工作流程。

- 明確步驟、完成條件、範例與有限次修正，減少模型需要自行推導的流程。
- Python 3.9+ 標準函式庫 runner：單一驗證入口、JSON 回饋、timeout 與錯誤退出碼。
- 例外 helper：驗證 rule、檔案 scope、reason、owner、expires；只豁免指定 finding。
- 不需要 `pip`、`uv`、`npm` 或 `go get`。專案本身的檢查工具須已存在。

### Codex / OpenCode 共用

將整個 `skills/harness-engineering/` 複製到目標專案的
`.agents/skills/harness-engineering/`。保留 `assets`、`scripts`、`references`。
若目標已有同名 skill，先比較內容再更新。

```text
<target-repo>/
  .agents/skills/harness-engineering/
    SKILL.md
    assets/
    references/
    scripts/
```

共用目錄與基本 `name` / `description` 格式依
[Codex 官方說明](https://learn.chatgpt.com/docs/build-skills) 與
[OpenCode 官方說明](https://opencode.ai/v2/docs/skills)。
本 repo 的 `skills/` 是套件來源目錄，不會自動成為 host 的 skill 搜尋目錄。

載入後可用自然語言呼叫：

> 使用 harness-engineering，把這個 repo 的架構規則轉成可執行 guardrail，沿用既有驗證入口，只用標準函式庫。

Codex 也可用 `$harness-engineering`。若尚未安裝，可以明確要求 agent 讀取上述 `SKILL.md` 後執行。

### 驗證附件

在此 repo 根目錄執行：

```sh
python3 -I -S skills/harness-engineering/scripts/test_verify.py
python3 -I -S skills/harness-engineering/scripts/test_exceptions.py
```

`-I -S` 隔離環境並略過 site packages；附件不依賴第三方 Python 套件。
runner 是命令執行器；具體架構、安全與合約規則由 skill 引導 agent 按專案實作。
模型行為的試跑方式見 [evaluation.md](skills/harness-engineering/references/evaluation.md)。
附件共 18 項測試通過。另以 GPT-5.6 terra 試跑小專案，依首次漏檢補強指引後通過 13 項專案測試；
詳細範圍與限制記錄於同一文件，Claude Sonnet-5 尚未實測。

## Resource Bootstrap skill

[`skills/resource-bootstrap/SKILL.md`](skills/resource-bootstrap/SKILL.md)
將 [`resource-bootstrap.md`](resource-bootstrap.md) 的方向轉成開發前的資源盤點與驗證流程。

- 從本次任務列出需求，查現有設定，只詢問缺口。
- 用 JSON 保存 endpoint、mirror 與 credential reference，分開設定、連線、認證與操作權限。
- 標準函式庫 CLI 提供 `analyze`、`list`、`resolve`、`check`、`doctor`。
- Pull proxy 與 push project 分開；`check --dockerfiles` 檢查實際 FROM 是否已使用解析後的完整路徑。
- doctor 有時間界線、不跟隨 redirect、不傳送 credential；401／403 不代表已取得操作權限。

將整個 `skills/resource-bootstrap/` 複製到目標 repo 的 `.agents/skills/resource-bootstrap/`，
即可使用上方的 Codex／OpenCode 共用格式。也可直接請 agent 讀取此 `SKILL.md`。

> 使用 resource-bootstrap，先盤點這次 container build 需要的資源，沿用 repo 已有的 registry 與 mirror，完成必要的 preflight 後繼續工作。

驗證附件：

```sh
python3 -I -S skills/resource-bootstrap/scripts/test_resource.py
```

預設測試使用暫存檔案與模擬的傳輸回應，不開 socket。工具不需要第三方套件。
另有 opt-in 的本機 HTTP 整合測試：在允許本機連線的環境下，以 `RESOURCE_TEST_NETWORK=1` 執行同一測試命令。
Dockerfile gate 的涵蓋範圍與全域網路限制的界線見 [images.md](skills/resource-bootstrap/references/images.md)；
模型試跑方式與紀錄見 [evaluation.md](skills/resource-bootstrap/references/evaluation.md)。
本次 17 項離線測試通過，1 項 HTTP 整合測試因權限請求未完成而未執行。
GPT-5.6 terra（medium）已完成 Go container 範例的 profile、mirror 改寫與離線 gate 試跑。

### Bridge Mode：既有專案遷移

同一個 skill 也實作了 [`bridge-resource.md`](bridge-resource.md) 的方向，入口為
[Bridge Mode](skills/resource-bootstrap/references/bridge.md)。沿用 Setup 的 resource profile，不另外維護一份 registry resolver。

> 使用 resource-bootstrap 的 Bridge Mode，把這個 repo 的外部依賴來源改成我提供的內部資源；先檢查 plan，再套用並驗證，缺少 mapping 時不要猜。

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge analyze
python3 <skill-dir>/scripts/resource.py --root <repo> bridge plan
python3 <skill-dir>/scripts/resource.py --root <repo> bridge plan --save
python3 <skill-dir>/scripts/resource.py --root <repo> bridge apply
python3 <skill-dir>/scripts/resource.py --root <repo> bridge verify --static
python3 <skill-dir>/scripts/resource.py --root <repo> bridge restore --transaction <id>
```

- plan 預設唯讀；apply 比對設定、檔案雜湊與精確位置，保留 lock、備份及還原能力。
- 內建支援 Dockerfile FROM、npm registry、npm lockfile artifact、HTTPS submodule、簡單 curl／wget。
- 保留 package identity、版本及 integrity；未知映射與不支持的格式阻擋改寫。
- Go／Python／Cargo／Maven、CI YAML 與複雜腳本需按專案補 native adapter；不可用字串批次替換假裝完成。
- `verify --static` 只驗證支援的靜態形式；`verify` 預設非零並明列 runtime 未執行。
  真正的隔離 build／test 必須有 host 網路限制及實際 witness，詳見 [runtime 指引](skills/resource-bootstrap/references/bridge-runtime.md)。

Bridge 的程式附件同樣只使用 Python 3.9+ 標準函式庫，必須保留整個 `scripts/` 目錄。

```sh
python3 -I -S skills/resource-bootstrap/scripts/test_bridge.py
python3 -I -S skills/resource-bootstrap/scripts/test_bridge_io.py
```

Bridge 的 24 項離線測試通過；GPT-5.6 terra（medium）已試跑支援範圍內的 plan／apply／static verify，
也確認遇到尚未支援的 npm build context 會保留阻擋。完整範圍與限制見
[試跑紀錄](skills/resource-bootstrap/references/evaluation.md)。
