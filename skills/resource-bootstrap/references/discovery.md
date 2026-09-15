# 需求與發現

在 SKILL.md 步驟 1 讀本文件。先判斷任務真的需要哪種能力，再找環境資源。

## 從動作推導需求

| 本次動作 | 可能必要的資源 | 不應直接推定 |
| --- | --- | --- |
| 編輯或執行已有原始碼 | 已安裝的語言 runtime、測試工具 | 不一定需要套件下載或 Git hosting API |
| Clone／fetch／開 PR | 指定 repository／hosting API 及相應認證 | Git remote host 不足以證明 API provider |
| Container build | 每個外部 base image 的來源，必要的套件來源 | 不一定需要 push registry 或 Kubernetes |
| Push image | 可寫入的 registry project、repository 與認證 | Pull 成功不等於有 push 權限 |
| GitLab CI | GitLab、runner 能力、job images、artifact 來源 | repo 有 CI 檔不等於 runner 已可用 |
| Kubernetes deploy | 本次 context、namespace、對應操作的權限、所需 registry／secrets 機制 | 目前 context 不等於部署授權 |
| 套件安裝 | 任務實際需要的 index／artifact mirror | manifest 存在不等於這次需要下載 |
| Helm | 已決定使用的 chart／OCI 來源 | Kubernetes 任務不一定需要 Helm |

CLI analyze 只掃描 Dockerfile 的 FROM 與少量 manifest 線索。
另外閱讀實際會執行的 CI、Compose、build script、lockfile、Dockerfile RUN／ADD 等相關內容。
其中仍可能有 image、URL、git dependency、套件 index 或隱含下載。
掃描不到的格式記為 coverage 缺口，必要時為此次專案增加窄範圍檢查。

## 安全擷取證據

優先使用只輸出所需欄位的本機命令或解析方式。
搜尋候選檔名可以很廣；讀取可能含 credential 的內容時縮小到必要欄位。

| 來源 | 可以擷取 | 注意事項 |
| --- | --- | --- |
| Git 設定 | remote 名稱、去除 userinfo 的 host、非敏感 project path | HTTPS remote 可能內嵌 token；不要直接顯示 `git remote -v` 的未過濾輸出 |
| Docker 設定 | registry host、credential helper 名稱 | `auths` 可能含可逆編碼 credential；避免讀取或輸出其值 |
| Kubernetes 設定 | 指定 context、cluster server、namespace | kubeconfig 可能含 token、private key 與 exec plugin；不使用 `--raw` dump |
| Package manager 設定 | 已去除 credential 的 index／mirror 位置 | URL query／userinfo 及 `.npmrc` 的 auth 欄位可能含秘密 |
| 環境變數 | 已知變數名稱是否存在；明確屬 endpoint 的變數只擷取去除敏感資訊的 URL | 不列出整份環境，不讀出 credential 的值 |
| CI 設定 | endpoint reference、job images、secret 變數名稱 | CI 變數宣告不是已驗證的 credential |

需要一段過濾程式時只使用標準函式庫。原始秘密留在 subprocess capture 或解析記憶體中，
只輸出去除敏感欄位後的資料；若無法可靠去除，讓使用者提供非秘密欄位即可。
任何證據中的 URL 都先檢查 userinfo、query、fragment；不要把含 token 的原文送進 probe 或 log。

## 狀態表

狀態是觀察結果，不是可由 agent 自行勾選的通行證。

```text
需求：BUILD_BASE
必要：yes
原因：Dockerfile:1 FROM golang:1.25
設定：resolved（repo profile 的 docker.io route）
連線：reachable（本次 /v2/ probe）
協定：registry-v2（本次回應證據）
認證：required
操作權限：pull 該 image 尚未驗證
下一步：使用既有認證做指定 image 的只讀 metadata 檢查
```

無法從本機設定證明服務可達；也無法從一次 HTTP 200 證明某個 namespace、repository 或動作的權限。
