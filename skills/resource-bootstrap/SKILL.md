---
name: resource-bootstrap
description: 開發、建置、CI 或部署需要 Git、registry、mirror、套件來源或 Kubernetes 時，先盤點、解析並驗證資源；建立不含秘密的 resource map，避免誤用工具預設端點。也用於診斷資源設定缺口與鏡像來源。
---

# Resource Bootstrap

先確認任務要使用哪些資源，再執行依賴它們的命令。
產物是有來源依據的 resource profile、任務需求表與驗證紀錄。

## 執行方式

- 支持 Codex、OpenCode 或具有讀檔、編輯與終端機能力的 agent；單一 agent 即可完成流程。
- 文件連結與 `scripts/resource.py` 相對於 **skill 目錄**；`--root` 指向 **目標 repo**。
- CLI 使用 Python 3.9+ 標準函式庫。使用 `python3`，Windows 可用 `py -3`。不需要 `pip`、`uv`、`npm` 或 `go get`。
- CLI 的 analyze、list、resolve、check 都離線；只有 doctor 會連線。工具不改寫 repo、登入服務、下載映像或部署。
- 先看命令實際會接觸的端點；既有設定及使用者已給的授權可以沿用。
  工具的預設公共端點本身不構成任務選擇。未知端點先解析，有 internal mirror 時按已確認政策使用。

## 1. 從任務列出必要能力

讀取 repo 指引、使用者計畫及相關建置／CI 檔案。先讀 [需求與發現](references/discovery.md)。
以任務為準；例如只編輯程式不一定需要 registry，只有實際部署才需要 cluster 與 namespace。

執行離線線索掃描：

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> analyze
```

把線索與任務整合成需求表，每項寫出 `id`、`required`、`kind`、`value`、`reason`、`source`。
將必要性與狀態分開：必要資源也可能尚未設定，已設定的資源也可能與任務無關。
analyze 是有明確涵蓋範圍的線索掃描，不能代替閱讀任務或推斷完整 CI 執行環境。

**完成條件：** 每項本次必要能力有原因與來源；無關能力不會形成阻礙。

## 2. 找既有設定，處理衝突

讀 [profile 與 CLI](references/profile.md)，再查：

1. 使用者本次明確選擇及 repo 對此工作的設定。
2. 既有 resource profile 與有關的 local profile。
3. 相關工具的設定、Git remote、指定的 Kubernetes context 等本機證據。
4. 任務已知的 endpoint 環境設定；credential 則只查 reference 名稱與是否存在，不讀出秘密值。

由高到低找候選；若證據互相衝突，記錄雙方來源，確認本次要用哪一個。
Git SSH host 不等於已確認的 GitLab HTTPS API；Harbor host 不等於已建立 Docker Hub proxy project。
目前的 Kubernetes context 也不等於已授權的部署目標。

read-only 評估任務只產出盤點與建議。建立／修復任務則用附件 JSON 範例填入真實設定。
本 skill 用 `.agent/resources.json` 與 `.agent/resource-plan.json`；已有等價機制時沿用其用途與 ownership。

**完成條件：** 每個選定值都有證據；尚未解決的缺口與衝突有獨立紀錄。

## 3. 一次詢問真正缺少的資訊

先完成可從本機查得的部分，再將缺口合併成一個問題，例如：

```text
已確認：GitLab host、Harbor host。
仍需要：
1. Docker Hub proxy project 的名稱及確認依據。
2. 這次部署的 Kubernetes context 與 namespace。
認證只需既有登入方式或環境變數名稱；請勿貼上 token 或密碼。
```

使用者已提供的選擇不必再問。若已明確允許公共來源，記錄其範圍並使用；不額外套用一輪核准。
必要答案尚未到達時，繼續不依賴它的工作，保留依賴該資源的操作。
profile 內的 evidence 是來源紀錄，不是可替代使用者授權的批准旗標。

**完成條件：** 可獨立處理的工作完成；缺少資訊只詢問一次並明確指向下一步。

## 4. 離線解析，再做有限探測

先執行 configuration check：

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> check
```

涉及 container image 時，先讀 [映像解析與檔案 gate](references/images.md)。
需要連線驗證時，先讀 [probe 與能力證據](references/probes.md)，再對已確認資源執行 doctor。
只讀指定資源的 metadata；沿用工具認證時確認命令不會洩露 credential 或執行不明 credential plugin。

每次失敗先分類並提出新假設。每個資源預設最多 2 次 probe；只有端點、CA、路由或認證條件改變才重試。
DNS／timeout 不自動切換公共網路，TLS 失敗不關閉憑證驗證，401 不觸發自動登入或下載。
達上限時保留證據與缺少條件，完成其他獨立工作。使用者有不同預算時沿用其指示。

**完成條件：** 設定、連線、協定、認證與操作權限各自有實證或明列未驗證；整體狀態不超過證據。

## 5. 保存映射並接到實際工作

在已授權建立／修復的任務中，保存去除秘密的 profile 與需求表。
local 設定依 [profile 規則](references/profile.md) 處理；保留未知欄位屬於原工具的檔案，避免整份覆蓋。
將 resolved endpoint 寫入這次會用到的 Dockerfile、CI 或建置設定，再檢查實際產物。
若涉及 Dockerfile，可執行：

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> check --dockerfiles
```

可將 CLI 複製到 repo 的 `tools/resource.py`，把離線 check 接入既有 `make verify`。
需要線上 doctor 時使用既有 preflight 入口或獨立的 `make doctor`；不要讓離線測試悄悄增加網路依賴。
本 gate 只檢查它明列支持的資料與 Dockerfile；任意程序的網路限制需要 host 的 sandbox／egress policy。

交付時列出：選定資源與證據、實際檢查 exit code、未驗證能力，以及接續工作的命令。
原任務包含開發工作且必要資源已可供該步驟使用時，接續原任務；只要求 bootstrap 時交付設定即可。

**完成條件：** 該步驟要用的必要資源已解析、所需能力已驗證、實際設定使用選定映射。
尚缺 push／部署權限時，可以完成本機工作，但不能宣稱 push／部署已準備完成。

## 維護本 skill

修改 CLI 後執行 `python3 -I -S scripts/test_resource.py`（相對 skill 目錄）。
調整流程後，依 [行為試跑](references/evaluation.md) 在隔離環境驗證。
