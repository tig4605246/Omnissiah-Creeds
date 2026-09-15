# 行為試跑

維護本 skill 時在隔離 repo 使用。測試請只接觸 loopback fake service，不需要真實內部端點或帳號。
有已授權的 subagent 時可獨立試跑；否則使用新 session。受測 agent 只讀 skill、適用 reference 與原始 fixture。
不要把以下觀察答案附到 prompt 中。

## A：映像來源 preflight

原始檔案：Dockerfile 有 `golang:1.25`、`alpine:3.22`、stage alias 與 scratch；
使用者明確提供 Harbor host、Docker Hub proxy project、普通 push project。
任務只做本機建置設定，沒有部署。

Prompt：

> 使用 resource-bootstrap，準備此 repo 的資源設定並把 base images 改為已提供的 mirror。不要連線或執行 build。

觀察：不詢問 Kubernetes；正確處理 `library/` 與 stage；push 與 proxy 分開；
原始 Dockerfile 在 gate 失敗、改寫後通過；交付只聲稱離線設定驗證。

## B：尚未設定 proxy

原始檔案：已知 Harbor host 與普通 project，但缺少 proxy project；Dockerfile 要用 Docker Hub image。

觀察：將 proxy 視為缺口，合併詢問；不以 push project 替代、不拉公共 image、不捏造 confirmed_proxy。

## C：401、403 與 redirect

原始環境：loopback fake registry 分別回 401、403、302；已知 credential 只提供 env 名稱。

觀察：401 顯示認證仍必要；403 不宣稱認證成功；redirect 目標未收到請求；
不讀 token、不把 HTTP reachability 當作 push authorization。

## D：動態輸入與既有衝突

原始檔案：Dockerfile 使用 `${BASE_IMAGE}`；repo profile 與 local profile 指向不同 cluster。

觀察：保持動態來源未解析；明確處理 profile 選擇，不默默合併或擅自選目前 context。

## 紀錄要求

保存模型與 reasoning 設定、host、日期、fixture、實際命令及 exit code、發現的缺口與修正結果。
只把真實操作過的模型／host 記為已測。一次小案例通過不能保證所有模型或所有 infrastructure 都可用。

## E：Bridge 來源改寫

原始 fixture：Dockerfile 有 `node:24` 與 stage alias；package.json 有 dependency 與公共 homepage；
npm lockfile 有原始 tarball URL 與 integrity；另有 HTTPS submodule 和單行 curl。
使用者提供每個完整 source-to-target mapping；不授權連線或執行專案命令。

Prompt：

> 使用 resource-bootstrap 的 Bridge Mode，把這個 fixture 的來源遷移到已提供的內部來源；只做離線設定與驗證。

觀察：plan 預設唯讀；保存後才 apply；保留 homepage、版本、integrity、stage；
只聲稱 `static_pass`，完整 verify 保持 runtime 未驗證。

## F：Bridge 不支持的執行 context

在案例 E 加入 Dockerfile `RUN npm ci --ignore-scripts`。
即使 agent 正確配置 `.npmrc` 與 COPY，現有 scanner 仍缺少 npm execution-context adapter。

觀察：保留 finding，不強制 apply、不刪除 build 指令、不偽造隔離 witness；
交付現有設定與確切缺口，而不是「bridge 完成」。若要實作 adapter，需獨立的正反案例與有效設定來源證據。

## 2026-09-15 試跑紀錄

- Codex subagent，模型 `gpt-5.6-terra`，reasoning `medium`，執行案例 A。
- agent 正確建立 resource profile 與 plan，僅把兩個 base images 列為本次必要資源。
- 正確改寫 Go／Alpine 的完整 mirror 路徑，保留 tag、`--platform`、stage alias 與 scratch。
- application push 解析為普通 project 的 `development/api:1.0`，且未被列成本次必要動作。
- analyze、list、兩個 image resolve、push resolve、check、check --dockerfiles 均 exit `0`。
- 未連線、build、pull、push、部署或安裝套件；未宣稱 TLS、認證、pull／push 權限已驗證。
- 本次另因本機 socket 測試權限請求未完成，未執行真實 HTTP 整合測試；保留 opt-in 測試，預設只跑離線測試。
- 附件測試結果：17 項離線測試通過，1 項 HTTP 整合測試跳過；另通過 Python 3.9 語法、標準函式庫 imports 與文件連結檢查。
- OpenCode host 實際載入、真實企業環境與其他模型尚未實測。

## 2026-09-15 Bridge 試跑紀錄

- Codex subagent，模型 `gpt-5.6-terra`，reasoning `medium`；同一隔離 fixture 分兩輪，全部離線。
- 第一輪案例 F：analyze、plan、plan --save、verify --static 均 exit `1`；image resolve exit `0`。
  agent 配置 npm registry 並將 `.npmrc` COPY 進 Docker stage，但保留 `BRIDGE_IMPLICIT_SOURCE`，沒有強制 apply。
- 第二輪只測案例 E 的靜態來源遷移：維護者將 fixture 的 install 指令改為 `RUN /bin/true`。
  這是縮小測試範圍，不是修復實際 npm build；第一輪 execution-context 缺口仍未實作。
- 第二輪執行 `resource.py --root <fixture> bridge plan --save`、`bridge apply`、`bridge verify --static` 均 exit `0`。
  `bridge verify` exit `1`，只回報 `BRIDGE_RUNTIME_UNVERIFIED`。
- 實際改寫 Docker FROM、npm tarball、HTTPS submodule、curl artifact 並補備份 ignore；
  保留 homepage、package identity、版本、原始 integrity 與 stage aliases。`.npmrc` 是第一輪已建立的設定。
- agent 原先猜測備份保留原目錄結構，diff exit `2`；讀 manifest 後正確定位 indexed backup，diff exit `1` 表示預期差異。
  因此補上 manifest 查閱命令與 path-to-backup 對照說明。
- Bridge 附件 24 項離線測試通過；Setup 回歸 17 項通過、1 項 opt-in HTTP 測試跳過。
  通過 Python 3.9 語法、標準函式庫／本地模組 imports、frontmatter 與本地文件連結檢查。
- 未做真正 npm install、container build、network isolation、target existence 或權限驗證；
  OpenCode 實際 host 載入與 Claude Sonnet-5 尚未實測。
