# Probe 與能力證據

開始 doctor 或其他連線檢查前讀本文件。

## 一次 probe 的界線

doctor 只對所選 profile resource 執行一次未認證的 HTTP GET。
registry 使用 `/v2/`；其他 resource 必須明確提供 `probe_path`。
它不跟隨 redirect、不前往 Bearer challenge 的 realm、不讀取 credential 值，也不登入。
保留 TLS 憑證驗證；環境需要特殊 CA／proxy 時，使用已確認的工具配置做有界限檢查並記錄，不能關閉驗證。

附件使用直接連線，不採用 ambient proxy 環境變數。預設 timeout 為 5 秒，上限 30 秒；
一次呼叫不代表整套認證／namespace／repository probe。
網路工具需取得 host 執行權限時，使用 host 的正常核准機制，不改用其他方法繞過限制。

## 如何解讀結果

| 觀察 | 可以推論 | 尚不能推論 |
| --- | --- | --- |
| DNS 失敗 | 名稱解析沒有完成 | 服務不存在、公共來源會成功 |
| 連線 timeout／TCP 失敗 | 未建立足夠的連線證據 | 一定是 firewall 或特定路由原因 |
| TLS 失敗 | 安全連線未完成 | 應該忽略憑證 |
| `/v2/` HTTP 200，加 `registry/2.0` header | Registry V2 base endpoint 可達 | 指定 image 存在或可 push |
| `/v2/` HTTP 200，沒有該 header | HTTP endpoint 可達；協定需額外證據 | 一定不是 registry，或完整符合 OCI |
| HTTP 401 | 有 HTTP 回應，通常需要認證 | 認證已完成或有目標操作權限 |
| HTTP 403 | 請求被拒絕；也可能由 proxy／policy 拒絕 | 認證已成功 |
| HTTP 404 | 該路徑未提供可用資源或被隱藏 | 整個 host 不存在 |
| Redirect | 目前端點要求前往別處 | 可自動使用新 host 或傳送 credential |

Docker Distribution 的 base probe 文件描述 200／401 與版本 header；
OCI 將 Docker-specific header 定義為 optional。
因此附件對缺少 header 的回應保留「協定未驗證」，不把它判成錯誤 registry。
[Docker API version check](https://distribution.github.io/distribution/spec/api/#api-version-check)、
[OCI legacy headers](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)

doctor 的 `authorization` 保持未檢查。HTTP／TLS 成功不代表 bootstrap 的全部條件都通過。
401 可以同時是「連線成功」與「仍需認證」；不能只用一個 pass／fail 遮掉這個差異。

## 按任務驗證真正要用的能力

使用已存在且經確認的工具認證，查詢本次指定資源的只讀 metadata：

- Image pull：指定 repository、tag／digest 的 manifest 與授權結果。
- GitLab：指定 project 與本次需要的 API scope。
- Kubernetes：本次 context、namespace 與指定 action 的 authorization 查詢。
- Push：普通 project 的 metadata 與可用的 permission 證據。只讀資訊無法證明時保持未驗證，不為了 doctor 試推映像。

這些命令不由附件自動產生或執行。先確認實際工具版本、認證位置與參數，避免載入不明 credential plugin。
只讀 probe 不授權 push、建立 namespace、修改遠端 project、部署或刪除資源。
使用者已授權的後續工作，可以在必要資訊完整時繼續；probe 不能擴張其範圍。

每個失敗回報 resource ID、來源、觀察、缺少的證據與下一個可驗證動作。
不輸出 response body、Authorization header、credential 或含秘密的完整 exception。
