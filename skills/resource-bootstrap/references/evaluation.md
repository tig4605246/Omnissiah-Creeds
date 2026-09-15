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
