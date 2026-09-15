# 映像解析與檔案 gate

涉及 container build／pull／push 時讀本文件。

## 先分開 pull 與 push

私有 project 是上傳產物的位置。Proxy cache project 對應 upstream image source；
Harbor proxy cache project 不能接受 push。不能因已知 Harbor host 就假設已設置 proxy。
[Harbor proxy cache 文件](https://goharbor.io/docs/edge/administration/configure-proxy-cache/)

`image_routes` 的每筆設定必須有 `registry_resource`、`namespace`、`confirmed_proxy: true`、`evidence`。
確認依據來自本次使用者選擇、可信 repo 設定，或已授權的 Harbor project metadata 檢查。
`confirmed_proxy` 是可追溯聲明，不能由 agent 在沒有證據時自行填 true。
`push_targets` 的每筆設定只有 `registry_resource`、`namespace`，指向可供本次使用的普通 project。

## 解析規則

假設已確認 `docker.io` 經由 `registry.example.internal/dockerhub`，push project 是 `development`：

| 輸入 | 解析後 |
| --- | --- |
| `ubuntu:24.04` | `registry.example.internal/dockerhub/library/ubuntu:24.04` |
| `team/app:1.2` | `registry.example.internal/dockerhub/team/app:1.2` |
| `docker.io/library/ubuntu@sha256:<64位hex>` | 保留同一 digest，改成已確認的 cache 路徑 |
| Push `api:1.2` 至 application target | `registry.example.internal/development/api:1.2` |

Docker Hub 的官方單層名稱需要 `library/`。保留使用者提供的 tag／digest；沒有 tag 時 CLI 明確補 `latest`。
任務要求 reproducible build 時，另取得並保存已驗證 digest；字串解析不會查詢遠端 digest。
[Harbor 映射規則](https://goharbor.io/docs/edge/administration/configure-proxy-cache/)

已完全指定且位於已知 proxy／push project 的 image reference 可再次解析，結果應相同。
只有 `direct_registries` 明列的 registry 可以直接保留；沒有 route 或直接使用依據時回報缺口。
不將未知 upstream、相近 hostname、namespace 或 tag 猜成已知值。

CLI 支持常見 Docker reference 與 SHA-256 digest 的明確子集。
遇到未支持的 digest 或 reference 語法時回報缺口；不要擅自刪除 digest、改 tag 或套用相似 registry。
Docker 的 shorthand／default registry 是命名慣例；OCI manifest API 的 tag／digest 規範是不同層次。
[Docker reference 實作](https://github.com/distribution/reference/blob/main/normalize.go)、
[OCI Distribution 規範](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)

## Dockerfile 分支

- `FROM scratch` 不會 pull。
- `FROM build` 若指向先前 stage alias，不會對外 pull。
- `FROM --platform=... image AS build` 仍要解析該 image。
- 多行 continuation 需要一起解析。
- 附件遇到 heredoc、非預設 escape directive、損壞的 FROM 或沒有 Dockerfile 時會回報缺口，
  不用刪除既有建置語意來遷就檢查器；有需要時接入能完整解析該語法的專案檢查。
- `FROM ${BASE_IMAGE}`、`FROM image:${TAG}` 等動態 reference 保持未解析；不要猜 ARG、CI 或 build-arg 的值。
  先找到本次實際輸入，再產生可驗證的確定設定或專案專用檢查。

先用 resolve 得到 reference，再用 agent 的編輯工具修改實際 Dockerfile。
執行 `check --dockerfiles`：原始 FROM 必須已是 resolved reference，單有 profile 映射不會使 Docker 自動使用 mirror。
保留 image 的版本、stage alias 與其他建置語意。

## 明確的 coverage 邊界

附件 gate 只處理其支持的 Dockerfile FROM 形式與名稱。
Compose image、GitLab services、BuildKit frontend／external COPY、Dockerfile RUN／ADD、
package install、任意 shell 命令及隱含網路請求，需要各自解析與驗證。
遇到這些來源時，在需求表與交付中列明，依本次任務補上檢查或使用 host 的 egress policy。
CLI 不是 Docker wrapper，也不會攔截所有網路流量。
