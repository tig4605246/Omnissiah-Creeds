# Native transport 的處理方式

只有遇到相應 ecosystem 或 unsupported finding 時，才讀取該節。
表中的設定是要核對的機制；不能只把範例寫入一個沒有 consumer 使用的檔案。
新增的 helper 仍只用標準函式庫。執行專案既有 package manager 需符合本次任務授權與已確認的內部來源。

## npm

保留 package identity，設定有效的 `.npmrc` default 與 scoped registry。
同時查看 lockfile 的 `resolved`、Git／HTTP specs 與 lifecycle scripts。
更改 registry 不一定會改變既有 custom-registry lockfile 的下載位置，因此單改 `.npmrc` 不足以證明 transport 已全部轉換。
[npm registry](https://docs.npmjs.com/misc/registry/)、[lockfile resolved](https://docs.npmjs.com/cli/v7/configuring-npm/package-lock-json/)

容器階段或 CI job 必須實際收到該設定。只在 host 建立 `.npmrc`，而 Dockerfile 沒有 COPY／mount／ENV 對應設定，不能宣稱 container install 已橋接。

## Python

使用有效的 `PIP_INDEX_URL`／pip config；保持 package 名稱與版本。
核對 CLI flags、requirements、`--extra-index-url`、`--find-links`、VCS 與 `name @ URL`。
pip 對多個 index 不保證優先使用內部來源；extra index 與 direct reference 必須各自處理。
[pip install](https://pip.pypa.io/en/stable/cli/pip_install/)、[requirements 格式](https://pip.pypa.io/en/stable/reference/requirements-file-format/)

不要建立 `.env.bridge` 後就結束；必須修改並驗證真正的 consumer，讓設定進入該 shell／CI／容器環境。

## Go

不改 `go.mod` 的 module identity；使用明確的 `GOPROXY` transport。
逗號與豎線有不同 fallback 條件，`direct` 會允許 VCS 存取。
`GOSUMDB` 是另一個來源，另核對 `GOPRIVATE`、`GONOPROXY`、`GONOSUMDB` 與 toolchain 下載。
不能為了消除連線而直接關閉 checksum 驗證。
[Go modules reference](https://go.dev/ref/mod)、[dependency management](https://go.dev/doc/modules/managing-dependencies)

## Cargo

使用 native source replacement，保留 crate identity 與 checksum。
replacement 必須提供相同內容；它不等於 dependency patch 或私有 registry。
sparse registry prefix、index metadata 中的 download URL 與 lockfile 都要檢查。
[source replacement](https://doc.rust-lang.org/cargo/reference/source-replacement.html)、[registry index](https://doc.rust-lang.org/cargo/reference/registry-index.html)

Python 3.9 沒有 TOML parser；環境若沒有既有 native parser，應使用受支持 runtime 的標準 parser，或明列缺口。
不要用幾個正則式宣稱完整支持 Cargo TOML。

## Maven／Gradle／NuGet

使用 native repository／mirror 機制。Maven mirror selection 是按 repository ID／`mirrorOf`，不是 hostname 取代。
核對 dependencies、plugins、profiles、parent／super-POM 與 effective settings；保持 artifact identity。
[Maven mirrors](https://maven.apache.org/guides/mini/guide-mirror-settings.html)、[Maven settings](https://maven.apache.org/ref/3-LATEST/maven-settings/settings.html)

Gradle 與 NuGet 同樣需要核對實際版本、plugin／bootstrap 來源及 config precedence；用已安裝工具的 native inspection 取得證據。

## OS repository／CI／Compose／Kubernetes／Helm

`apt-get update` 或 `apk add` 代表 base image 裡的來源；需要查該 image 的實際 distro 與 repository config。
不可只看到 apt 就寫死 Debian host。保留 suite、component、architecture、簽章與 key 的驗證機制。

YAML／Helm template 需要可理解 anchors、merge、變數與 chart conventions 的 parser／renderer。
除了 `image`，還要查 CI services、initContainers、hooks、include、runner helper、Helm repository 與 registry credentials 的引用方式。
原生展開結果與原模板都要有對應，不能只改看得到的一行。
