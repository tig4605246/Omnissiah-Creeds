對，我會把它設計成 `resource-bootstrap` 裡的一個獨立 **Bridge Mode**，而且它的責任要比前面的 setup mode 更進一步：

> **Setup Mode：告訴 Agent「這個環境有哪些可用資源」。**
> **Bridge Mode：把原本假設可以存取 Internet 的專案，實際轉換成只能依賴內部資源也能工作的專案。**

我會稱它為：

```text
resource bridge
```

或 Skill 裡的：

```text
mode: bridge
```

整體流程是：

```text
Existing Project
      │
      ▼
External Source Discovery
      │
      ▼
External Source Graph
      │
      ▼
Internal Endpoint Mapping
      │
      ├── resolved
      ├── unresolved → ask user
      └── unsupported → explain
      │
      ▼
Bridge Plan
      │
      ▼
Project Rewrite
      │
      ▼
Isolated Verification
      │
      ▼
PASS / Structured Feedback
```

而且我要特別加一條設計原則：

> **Bridge Mode 不允許猜測 mapping。**

例如看到：

```text
docker.io/library/node
```

不能因為知道有 Harbor 就自己假設：

```text
harbor.internal/dockerhub/library/node
```

必須先有明確 mapping，或詢問使用者。

---

# 1. Bridge Mode 真正要掃描的是「Source」，不是 URL

如果只做：

```bash
grep -R 'https://' .
```

一定漏很多。

例如這些全部都代表 external source：

```dockerfile
FROM node:24
```

```yaml
image: postgres:18
```

```json
"registry": "https://registry.npmjs.org/"
```

```bash
pip install fastapi
```

```bash
go mod download
```

```xml
<repository>
    <url>https://repo1.maven.org/maven2</url>
</repository>
```

```bash
curl -L https://github.com/foo/bar/releases/download/v1/tool.tar.gz
```

```ini
[submodule "foo"]
    url = https://github.com/foo/foo.git
```

甚至：

```dockerfile
RUN apt-get update
```

背後仍然代表：

```text
deb.debian.org
security.debian.org
```

所以 Bridge Mode 的核心抽象應該是：

```text
External Source
```

而不是：

```text
URL
```

---

# 2. 建立 External Source Graph

Agent 分析 repo 後產生：

```text
PROJECT
│
├── Container Images
│   ├── docker.io/library/node:24
│   ├── docker.io/library/alpine:3.22
│   └── docker.io/library/postgres:18
│
├── Package Registries
│   ├── registry.npmjs.org
│   └── pypi.org
│
├── Git Sources
│   ├── github.com/example/library
│   └── gitlab.com/foo/bar
│
├── Artifact Downloads
│   └── github.com/.../releases/tool.tar.gz
│
├── OS Packages
│   └── deb.debian.org
│
└── CI Services
    └── docker:dind
```

同時記錄：

```text
source
location
consumer
resolution mechanism
```

例如：

| Source                      | Found at                    | Consumer       | Type             |
| --------------------------- | --------------------------- | -------------- | ---------------- |
| `docker.io/library/node:24` | `Dockerfile:1`              | Docker         | container        |
| `postgres:18`               | `compose.yaml:12`           | Docker Compose | container        |
| `registry.npmjs.org`        | `package-lock.json`         | npm            | package registry |
| `pypi.org`                  | `requirements.txt` implicit | pip            | package registry |
| GitHub release              | `scripts/install.sh:18`     | curl           | artifact         |
| `deb.debian.org`            | Docker base image           | apt            | OS repository    |

這個 graph 會成為後續 rewrite 的依據。

---

# 3. Bridge Mapping

使用者提供內部 endpoint 後，建立：

```yaml
version: 1

mode: bridge

policy:
  unmapped_external_source: deny
  public_fallback: deny
  preserve_original_source: true

mappings:

  container:
    docker.io:
      endpoint: harbor.internal.example.com/dockerhub

    ghcr.io:
      endpoint: harbor.internal.example.com/ghcr

  npm:
    registry.npmjs.org:
      endpoint: https://npm.internal.example.com

  python:
    pypi.org:
      endpoint: https://pypi.internal.example.com/simple

  maven:
    repo1.maven.org:
      endpoint: https://maven.internal.example.com/repository/maven-central

  git:
    github.com:
      endpoint: https://git.internal.example.com/github-mirror

  artifacts:
    github-releases:
      endpoint: https://artifacts.internal.example.com/github

  apt:
    deb.debian.org:
      endpoint: https://apt.internal.example.com/debian
```

我會把它叫：

```text
.agent/bridge.yaml
```

但另外產生：

```text
.agent/bridge.lock.yaml
```

記錄實際解析結果：

```yaml
sources:

  - id: SRC001

    original:
      type: container
      source: docker.io/library/node:24

    resolved:
      source: harbor.internal.example.com/dockerhub/library/node:24

    discovered_at:
      file: Dockerfile
      line: 1

    validation:
      reachable: true
      artifact_exists: true
```

這很重要，因為 `bridge.yaml` 是 **policy/configuration**，而 `bridge.lock.yaml` 是 **analysis result**。

---

# 4. 不同 Source 類型要用不同 Rewrite Strategy

這部分不能全部用字串替換。

### Container image

原本：

```dockerfile
FROM node:24
```

Bridge：

```dockerfile
FROM harbor.internal.example.com/dockerhub/library/node:24
```

Compose：

```yaml
services:
  db:
    image: postgres:18
```

變成：

```yaml
services:
  db:
    image: harbor.internal.example.com/dockerhub/library/postgres:18
```

GitLab CI：

```yaml
image: golang:1.25
```

變成：

```yaml
image: harbor.internal.example.com/dockerhub/library/golang:1.25
```

Kubernetes / Helm 也要一起處理。

---

### npm

這種反而不應該：

```text
修改 package.json 中每個 package
```

而是設定 transport：

```ini
registry=https://npm.internal.example.com/
```

例如建立：

```text
.npmrc
```

如此：

```bash
npm ci
```

仍然保持原本 dependency identity。

---

### Python

Python 也是同樣概念。

不要改：

```text
fastapi==...
```

而是讓：

```text
pip
```

走：

```text
PIP_INDEX_URL=https://pypi.internal.example.com/simple
```

例如 Bridge Mode 可以修改：

```text
.env.bridge
scripts/bootstrap.sh
.gitlab-ci.yml
Dockerfile
```

但不要把 token commit 進 repo。

---

### Go

Go module：

```bash
go mod download
```

不用修改 `go.mod`：

```text
github.com/foo/bar
```

應改成：

```bash
GOPROXY=https://go.internal.example.com
```

因為：

```text
module identity ≠ transport endpoint
```

這個概念很重要。

---

### Cargo

Cargo 本身就支援 source replacement，因此可以建立：

```toml
[source.crates-io]
replace-with = "internal"

[source.internal]
registry = "sparse+https://cargo.internal.example.com/index/"
```

---

### Maven / Gradle

最好透過 repository / mirror configuration 解決。

不是把：

```text
groupId
artifactId
version
```

修改掉。

---

### Git

這個要分兩種。

如果：

```text
git clone https://github.com/foo/bar.git
```

可轉：

```text
https://git.internal.example.com/github-mirror/foo/bar.git
```

Git submodule：

```ini
[submodule "library"]
    path = third_party/library
    url = https://github.com/foo/library.git
```

也可以 rewrite。

但必須確認 mirror 裡真的存在這個 repository。

---

### Direct artifact download

這是最麻煩的一類：

```bash
curl -L \
  https://github.com/foo/tool/releases/download/v1.2.3/tool.tar.gz
```

Bridge Mode 應該判斷成：

```text
DIRECT_ARTIFACT
```

然後要求 mapping：

```text
original:
github.com/foo/tool/releases/download/v1.2.3/tool.tar.gz

internal:
artifacts.internal/tools/foo/tool/1.2.3/tool.tar.gz
```

不能只把 hostname 替換掉，因為 artifact repository 的 path structure 很可能完全不同。

---

# 5. Bridge Mode 要分三種 resolution 類型

這會讓 Agent 很容易判斷什麼能自動修改。

```text
TRANSPORT_REWRITE

npm
pip
Go proxy
Maven mirror
NuGet
Cargo

↓

package identity unchanged
```

```text
SOURCE_REWRITE

Docker image
Git repository
Git submodule
Helm repository

↓

source location changes
```

以及：

```text
ARTIFACT_MAPPING

curl
wget
GitHub Releases
binary installers

↓

explicit artifact-to-artifact mapping required
```

第三種最不能亂猜。

---

# 6. Command Interface

我會做成：

```bash
resource bridge analyze
```

例如：

```text
$ resource bridge analyze

External dependency analysis

TYPE          SOURCE                         COUNT   STATUS
container     docker.io                     7       UNMAPPED
npm           registry.npmjs.org            1       MAPPED
python        pypi.org                      1       MAPPED
git           github.com                    3       UNMAPPED
artifact      github.com/releases           2       UNMAPPED
apt           deb.debian.org                1       UNMAPPED

14 external sources detected.
5 require mappings.
```

然後：

```bash
resource bridge plan
```

輸出：

```text
BRIDGE PLAN

Dockerfile
  node:24
    → harbor.internal/dockerhub/library/node:24

compose.yaml
  postgres:18
    → harbor.internal/dockerhub/library/postgres:18

.npmrc
  CREATE
    registry=https://npm.internal/

.gitlab-ci.yml
  SET
    GOPROXY=https://go.internal/

scripts/install.sh
  UNRESOLVED
    github.com/foo/bar/releases/...

No files changed.
```

這一點我非常建議：

> **`plan` 預設永遠不修改檔案。**

---

# 7. Apply 階段

確認 mappings 都完整後：

```bash
resource bridge apply
```

它才修改。

例如：

```text
Bridge changes applied:

MODIFIED Dockerfile
MODIFIED compose.yaml
CREATED  .npmrc
MODIFIED .gitlab-ci.yml
MODIFIED scripts/bootstrap.sh

12 sources rewritten
2 transport configurations added
0 unresolved sources
```

然後直接：

```bash
git diff
```

讓 Agent review 自己的修改。

---

# 8. 最重要的是 `bridge verify`

如果只有 rewrite，還不夠。

應該有：

```bash
resource bridge verify
```

做兩件事。

第一部分：

```text
Static verification
```

重新 scan repository：

```text
docker.io
github.com
ghcr.io
registry.npmjs.org
pypi.org
repo.maven.apache.org
deb.debian.org
...
```

但不是單純 blacklist，而是重新建 Source Graph。

例如：

```text
BRIDGE001

Unmapped external source detected

Source:
  https://github.com/foo/tool/releases/...

Location:
  scripts/install.sh:18

Type:
  DIRECT_ARTIFACT

Required:
  artifact mapping

Suggested fix:
  resource bridge map artifact ...
```

這就是你的 Harness 文件裡所說的 **actionable feedback**，而不是只回一個模糊的 failed。

---

第二部分：

```text
Runtime verification
```

在隔離網路條件下執行：

```text
dependency restore
container build
compile
test
```

例如：

```bash
resource bridge verify

Static source scan........ PASS
Container resolution...... PASS
npm restore............... PASS
Go modules................ PASS
Container build........... PASS
Tests..................... PASS

External endpoints used:
0

PASS
```

這才代表：

> **這個專案真的可以在 isolated environment 裡工作。**

---

# 9. 我甚至會讓 Bridge Verify 有「Network Witness」

單純 source scan 還是可能漏掉 dynamic dependency。

例如程式：

```python
url = decode(...)
requests.get(url)
```

或者安裝 script runtime 才產生 URL。

所以理想版本可以監看：

```text
DNS queries
TCP connections
HTTP CONNECT
```

最終報告：

```text
NETWORK OBSERVATION

Allowed internal endpoints:

harbor.internal
npm.internal
pypi.internal
gitlab.internal

Observed:
harbor.internal:443     ALLOWED
npm.internal:443        ALLOWED
api.github.com:443      BLOCKED

BRIDGE_NET001

Unexpected external connection

Process:
  scripts/download-tool.sh

Destination:
  api.github.com:443
```

這會比 grep 強很多。

---

# 10. Bridge Mode 的完整狀態機

```text
                 Repository
                     │
                     ▼
              Analyze Sources
                     │
                     ▼
             External Source Graph
                     │
                     ▼
               Resolve Mapping
                     │
             ┌───────┴────────┐
             │                │
         RESOLVED         UNRESOLVED
             │                │
             │             Ask User
             │                │
             └───────┬────────┘
                     ▼
                Bridge Plan
                     │
                     ▼
              Validate Targets
                     │
                     ▼
                  Apply
                     │
                     ▼
                Static Scan
                     │
                     ▼
              Isolated Runtime
                     │
               ┌─────┴─────┐
               │           │
              PASS        FAIL
               │           │
               │       Feedback
               │           │
               │        Repair
               │           │
               └─────◄─────┘
                     │
                     ▼
                Bridge Lock
```

這就是你文件所強調的 **Self-Correcting Loop**，不是期望 Agent 一次 rewrite 就完全正確。

---

# 11. Skill 可以正式增加 Bridge Mode

我會把前面的 `resource-bootstrap` 擴充成：

```markdown
# Resource Bootstrap

Modes:

setup
  Discover infrastructure required by a development task and resolve
  available internal resources.

bridge
  Analyze an existing project for external dependency sources and
  transform it to operate using provided internal resources.
```

Bridge 的核心規則：

```markdown
## Bridge Mode

Bridge Mode converts a project that assumes access to external
infrastructure into a project that can operate in an isolated
environment.

### Rules

Discover sources before modifying the project.

Do not infer internal endpoint mappings.

Do not silently fall back to external infrastructure.

Preserve dependency identity whenever the ecosystem supports
transport-level registry or proxy configuration.

Prefer native ecosystem configuration over source code rewriting.

Validate target resources before applying rewrites.

Direct artifact URLs require explicit artifact mappings.

Never embed credentials in generated project files.

After modification, rescan the repository.

Verification is incomplete until no unresolved external source remains.

When possible, perform dependency restoration, build and test using
only the resolved internal endpoints.

Produce actionable feedback for every unresolved or failed resource.
```

---

# 12. Setup Mode 與 Bridge Mode 的關係

最後整體 Harness 就會變得很完整：

```text
                    Development Task
                           │
                           ▼
                   RESOURCE SETUP
                           │
             What infrastructure exists?
                           │
                           ▼
                   Resource Profile
                           │
                           │
Existing Project ─────► BRIDGE MODE
                           │
                What external sources exist?
                           │
                           ▼
                External Source Graph
                           │
                           ▼
                Internal Source Mapping
                           │
                           ▼
                  Project Rewrite
                           │
                           ▼
                  Bridge Verification
                           │
                           ▼
                    Development
                           │
                           ▼
                     make verify
```

我會再多加一個非常重要的觀念：

> **Setup Mode 建立 Environment Capability Model；Bridge Mode 建立 Project Dependency Model。**

兩者交集：

```text
Project Dependency Model
        ∩
Environment Capability Model
        │
        ▼
Resolved Execution Environment
```

這其實比「幫 Agent 設定 Harbor URL」高一個抽象層級。

---

# 13. 最終我會把 Harness 的前置生命週期定義成

```text
DISCOVER
   ↓
SETUP
   ↓
BRIDGE
   ↓
VERIFY ENVIRONMENT
   ↓
DEVELOP
   ↓
VERIFY PROJECT
   ↓
DELIVER
```

其中：

```text
SETUP
```

回答：

> 我有哪些東西可以用？

而：

```text
BRIDGE
```

回答：

> 這個專案原本依賴什麼？如何讓它全部改走我能用的東西？

這會非常適合企業內網、restricted network、air-gapped development、on-prem environment。也正好延伸你現在 Harness 的思想：Agent 可以探索、修改、Retry，但「是否真的已經轉換成隔離環境可工作」要由 Harness 決定，而不是靠 Agent 自己宣稱完成。

我甚至會把 `bridge verify` 接進你原本的單一驗證入口，例如：

```bash
make verify

# internally
resource bridge verify
make compile
make lint
make test
make architecture
make security
```

這樣 Developer、Agent、CI 看到的是同一個 truth，符合你原本「Local truth = CI truth」的準則。

**如果把 Setup + Bridge 一起做好，我認為它會成為你這套 Harness 很有辨識度的一個核心能力：不是只管「Agent 怎麼寫 code」，而是連「Agent 如何理解並適應企業實際執行環境」都工程化。**
