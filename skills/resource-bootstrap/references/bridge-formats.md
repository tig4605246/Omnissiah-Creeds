# Bridge 格式與涵蓋範圍

Bridge CLI 使用 Python 3.9+ 標準函式庫。要搬到目標 repo 使用，將
`resource.py`、`bridge.py`、`bridge_io.py` 放在同一個 `tools/` 目錄。
原本只使用 setup 的 `resource.py` 可繼續獨立使用。

## CLI

```sh
python3 tools/resource.py --root . bridge analyze
python3 tools/resource.py --root . bridge --config .agent/bridge.json plan
python3 tools/resource.py --root . bridge plan --save
python3 tools/resource.py --root . bridge apply
python3 tools/resource.py --root . bridge verify --static
python3 tools/resource.py --root . bridge restore --transaction bridge-<id>
```

也可直接執行 `bridge.py --root <repo> --config .agent/bridge.json <command>`。
設定路徑相對 repo；bridge 不使用 setup CLI 的 `--profile`／`--plan`，而使用 config 裡的 `resource_profile`。

- analyze 不需要 bridge config，預設唯讀。
- plan 唯讀；只有 `--save` 才寫 `.agent/bridge.plan.json`。
- apply 預設讀上述 plan；`apply --plan <relative-file>` 可指定另一份 saved plan。
- 所有命令均不連線、安裝套件或執行專案命令。
- exit `0`：該命令範圍通過；`1`：存在來源／驗證缺口；`2`：設定、stale plan、寫入或恢復錯誤。

## Config v1

頂層必須包含 `schema_version: 1`、`resource_profile`、`mappings`。
`resource_profile` 是完整 setup profile 的 repo-relative 路徑；沒有 container 來源時可為 `null`。
Bridge profile 的 `direct_registries` 必須為空，container 使用明確內部 project。

每筆 mapping 必須包含：

| 欄位 | 意義 |
| --- | --- |
| `kind` | `npm`、`git` 或 `artifact` |
| `original` | 精確的來源 HTTP(S) URL |
| `resolved` | 精確的內部 HTTPS URL |
| `evidence` | 使用者確認、repo 規範或實際 target 檢查的來源 |
| `integrity` | 只有 artifact 需要：`sha256:<64位hex>` 或 lockfile 的 `sha512-<base64>` |

URL 不含 userinfo、query、fragment 或變數。artifact 的 target 必須是同一內容；
config 中的 integrity 是要求，實際內容仍需以可信 metadata／下載結果驗證。
若 lock 已有 integrity，映射必須與之相符；改 URL 不改 package identity 或 checksum。
映射採精確值比對，不支持 hostname prefix replacement、串接多層 mapping 或 guessed paths。

## 自動 adapter

| 格式 | 會辨識／修改 | 保留 |
| --- | --- | --- |
| Dockerfile FROM | 共用 setup 的 image parser 與 proxy mapping | tag、digest、stage alias、scratch、其他指令 |
| `.npmrc` | default／scope registry 的確定 URL | 其他非秘密設定、credential env reference |
| `package.json` | 發現 npm 預設 transport，必要時在同目錄建立／追加 `.npmrc` | dependencies、versions、name 與 metadata |
| package-lock／npm-shrinkwrap | JSON `resolved` tarball 的精確映射 | 版本、integrity、dependency identity 與其他欄位 |
| `.gitmodules` | 有效 submodule config 的單行 HTTPS URL | path、branch 與其他設定 |
| `.sh`／Dockerfile 的簡單 curl／wget | 單行、單一明確 URL 的 artifact-to-artifact 替換 | 選項、輸出檔名與其他行 |

含 inline credential 的 `.npmrc` 阻擋規劃，不把秘密值存進 plan。
Dockerfile RUN 的 package install、複合／多行下載、動態來源、直接 Git package dependency、
以及 YAML／TOML／XML native build config 會列為需要 adapter，不能默默算成已完成。
例如即使 `.npmrc` 已 COPY 進 stage，`RUN npm ci --ignore-scripts` 仍會阻擋；
目前 CLI 不解析該 stage 的完整有效設定。保留實際建置命令，補上有測試的 context adapter；
不能刪掉安裝命令來讓真實專案的 gate 通過。

## 邊界

scanner 有檔案數與單檔大小上限，越界會失敗。metadata、VCS、安裝後依賴、虛擬環境與 agent 指引不作為自動改寫目標。
binary 只保存雜湊；非忽略路徑的 symlink 檔案／目錄與非一般檔案會形成缺口。隱藏的使用者／環境配置、
Git hooks、動態產生的來源與未執行分支，需要 native audit 與 runtime witness。

請不要把這些限制寫成「整個 repo 已無外部來源」。
新增 adapter 時先準備會通過、會失敗與不支持語法的 fixtures；不能用通用字串替換代替語意解析。

## Plan／lock／backup

Plan 包含精確 edit spans、原值／新值、repo 與設定雜湊、來源表；apply 會重新生成整份 plan 進行比對。
Lock 記錄解析與交易結果，不記錄虛構的 reachable、artifact_exists 或 runtime success。
Backup 是本機恢復資料，可能含原始檔案中的敏感內容；保留私有檔案權限與內部 ignore，勿 commit。
產物任一方被改變時，先重新計算及核對，不能強制套用過期計畫。
