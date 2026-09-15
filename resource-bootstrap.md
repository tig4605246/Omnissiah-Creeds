這個方向很適合你現在的 Harness Engineering。關鍵是不要把它做成「Agent 開始失敗後才提醒它改 URL」，而是把 **Environment Discovery / Resource Resolution 變成所有開發工作的 Preflight 階段**。

你現有文件的核心其實已經支持這個方向：Prompt 負責表達意圖，但不可妥協的邊界應轉化為 executable guardrail；而且 Agent、Developer、CI 最好共用同一套驗證入口。 

我會把這個 Skill 命名為：

> **`environment-setup` / `resource-bootstrap`**

它不是單純 setup script，而是一個 **Resource Discovery Agent Skill**。

---

# 一、它要解決的問題

現在 Coding Agent 很容易做這種事情：

```text
Task:
Build a service and containerize it.

Agent:
docker pull golang:1.25
        ↓
docker.io/library/golang:1.25
        ↓
timeout
        ↓
retry
        ↓
retry
        ↓
開始 debug DNS / proxy / firewall
```

但其實你的環境可能是：

```text
Harbor
registry.dev.internal

GitLab
gitlab.dev.internal
```

甚至：

```text
Docker Hub Proxy Cache
registry.dev.internal/dockerhub
```

理想流程應該變成：

```text
Task / Development Plan
        ↓
Resource Analysis
        ↓
需要：
- Git repository
- Container registry
- Docker Hub upstream mirror
        ↓
Environment Profile 查詢
        ↓
GitLab ✓
Harbor ✓
Docker Hub proxy ?
        ↓
詢問 User
        ↓
Validate
        ↓
建立 Resource Map
        ↓
Agent 開始工作
```

也就是：

> **Resource resolution before execution.**

---

# 二、Skill 不應只詢問「你有什麼資源」

更好的方式是讓 Agent **從 development plan 推導 resource requirements**。

例如使用者說：

> 建立 Go API，Docker 化，建立 GitLab CI，最後部署到 Kubernetes。

Skill 分析後得到：

| Requirement          | Reason                            |
| -------------------- | --------------------------------- |
| GitLab               | repository / CI                   |
| Container Registry   | build artifact                    |
| Docker Hub mirror    | `golang:*`, `alpine:*` base image |
| Kubernetes API       | deployment                        |
| Kubernetes namespace | deployment target                 |
| Helm registry        | 如果使用 Helm                         |
| Secrets mechanism    | imagePullSecret / app secrets     |

然後才去看：

```text
目前知道什麼？
```

而不是盲目問一堆問題。

---

# 三、Environment Profile

我建議 repo 內建立：

```text
.agent/
├── resources.yaml
└── resources.local.yaml
```

其中 `resources.yaml` 可以 commit。

例如：

```yaml
version: 1

policy:
  resolution_mode: internal-first
  unknown_external_endpoint: ask
  never_guess_endpoint: true

resources:

  source_control:
    provider: gitlab
    base_url: https://gitlab.dev.example.com

  container_registry:
    provider: harbor
    registry: registry.dev.example.com

    push_namespace: development

    upstreams:
      dockerhub:
        type: harbor-proxy-cache
        namespace: dockerhub

  kubernetes:
    contexts:
      development:
        context: dev-cluster
        namespace: development

credentials:

  gitlab:
    token:
      source: env
      name: GITLAB_TOKEN

  harbor:
    username:
      source: env
      name: HARBOR_USERNAME
    password:
      source: env
      name: HARBOR_PASSWORD
```

非常重要：

### 不儲存 credential。

只儲存：

```yaml
source: env
name: HARBOR_PASSWORD
```

不要：

```yaml
password: MyPassword123
```

---

# 四、Resource Resolution Priority

Skill 應該有固定 resolution order：

```text
1. Repository configuration
2. Existing resource profile
3. Existing tool configuration
4. Environment variables
5. Git remote / kubeconfig 等 runtime evidence
6. Ask user
7. External endpoint
```

例如：

```text
Need GitLab
   ↓
git remote -v
   ↓
git@gitlab.dev.example.com:foo/bar.git
   ↓
GitLab endpoint discovered
```

所以不必再問：

> GitLab URL 是什麼？

---

# 五、最重要的一條 Guardrail

我甚至會直接加入：

```text
DO NOT access an external service merely because it is
the default endpoint of a tool.
```

包括：

```text
docker.io
github.com
registry.npmjs.org
pypi.org
repo1.maven.org
ghcr.io
quay.io
```

但不是完全禁止 Internet，而是：

```text
Known endpoint
    ↓
use it

Unknown endpoint
    ↓
resource resolver

Internal alternative exists
    ↓
use internal

No alternative known
    ↓
ask user
```

所以 Agent 不可以直接：

```bash
docker pull ubuntu:24.04
```

在不知道 registry policy 的情況下執行。

---

# 六、Harbor 要特別區分兩種用途

這一點很重要。

「有 Harbor」不代表：

```text
ubuntu:24.04
node:24
golang:1.25
```

就能從 Harbor pull。

Skill 必須知道：

### Private image namespace

例如：

```text
registry.company.local/myteam/myapp:1.2
```

以及：

### Proxy Cache

例如：

```text
registry.company.local/dockerhub/library/ubuntu:24.04
```

所以 schema 最好是：

```yaml
container_registry:

  registry: registry.company.local

  push:
    namespace: platform

  proxy_cache:

    dockerhub:
      namespace: dockerhub

    quay:
      namespace: quay

    ghcr:
      namespace: ghcr
```

如果 Agent 發現：

```dockerfile
FROM ubuntu:24.04
```

它可以解析成：

```text
source:
docker.io/library/ubuntu:24.04

resolved:
registry.company.local/dockerhub/library/ubuntu:24.04
```

而不是直接打 Docker Hub。

---

# 七、Ask User 的方式

Skill 不應該一個一個問。

應該一次完成 resource gap analysis。

例如：

> 這個開發計畫需要以下基礎設施：
>
> ✓ GitLab：已從 git remote 發現
> ✓ Harbor：已設定
> ? Docker Hub mirror：尚未設定
> ? Kubernetes deployment target：尚未設定
>
> 請提供：
>
> 1. Harbor Docker Hub Proxy Cache 的 namespace / URL
> 2. Kubernetes context 與 namespace
>
> Credential 不需要直接提供；若需要 authentication，請告訴我對應的 environment variable 名稱即可。

這比：

```text
What's your registry?
What's your GitLab?
What's your Kubernetes?
What's your token?
```

好很多。

---

# 八、Try-and-error 要變成 Structured Discovery

你提到希望它可以透過 **問答 + try and error**。

我會限制它只做 **bounded probing**。

例如 Harbor：

```bash
curl https://registry.company.local/v2/
```

結果：

```text
401
```

反而代表：

```text
DNS    PASS
TCP    PASS
TLS    PASS
Registry API PASS
AUTH   REQUIRED
```

而不是：

```text
ERROR 401
```

Skill 可以回報：

```text
RESOURCE_CHECK

resource: container-registry
endpoint: registry.company.local

dns: pass
tcp: pass
tls: pass
protocol: docker-registry-v2
authentication: required

status: reachable
```

---

# 九、錯誤應該分類

例如：

```text
RESOURCE_DNS_FAILURE
RESOURCE_TCP_FAILURE
RESOURCE_TLS_FAILURE
RESOURCE_AUTH_FAILURE
RESOURCE_PROTOCOL_MISMATCH
RESOURCE_PATH_INVALID
RESOURCE_PERMISSION_DENIED
RESOURCE_NOT_CONFIGURED
```

這完全符合你 Harness 文件裡對 actionable feedback 的要求：

> 錯誤不能只有 failed，而要包含 source、violation、expected、suggested fix。

例如：

```text
RES003: Container registry authentication failed

Resource:
  registry.dev.example.com

Connectivity:
  DNS: PASS
  TCP: PASS
  TLS: PASS
  Registry API: PASS

Failure:
  Authentication required

Expected credential:
  HARBOR_USERNAME
  HARBOR_PASSWORD

Suggested action:
  Configure these environment variables and run:
    resource doctor container-registry
```

這樣 Agent 可以自己修正。

---

# 十、我會把 Skill 設計成這個 State Machine

```text
              Development Plan
                     │
                     ▼
             Analyze Requirements
                     │
                     ▼
              Resource Graph
                     │
                     ▼
        ┌──── Resolve Existing ────┐
        │                          │
        ▼                          ▼
     resolved                    missing
        │                          │
        │                          ▼
        │                       Ask User
        │                          │
        └─────────────┬────────────┘
                      ▼
                   Validate
                      │
              ┌───────┴────────┐
              │                │
            PASS              FAIL
              │                │
              │         diagnose / retry
              │                │
              │         bounded attempts
              │                │
              └───────┬────────┘
                      ▼
               Resource Profile
                      │
                      ▼
                Development
```

---

# 十一、Skill 本身可以長這樣

我會先做成類似下面這份 `SKILL.md`：

```markdown
---
name: resource-bootstrap
description: >
  Discover, resolve, validate, and configure infrastructure resources
  required by a development task before implementation begins.
---

# Resource Bootstrap

Before executing a development plan, determine which external or
infrastructure resources the task requires.

The goal is to prevent agents from accessing assumed default endpoints
when organization-provided alternatives may exist.

## Core rule

Never assume that the default endpoint of a tool is reachable or allowed.

Examples include:

- docker.io
- github.com
- ghcr.io
- quay.io
- registry.npmjs.org
- pypi.org
- repo1.maven.org

Resolve resources before using them.

## Workflow

### 1. Analyze the development plan

Infer required capabilities.

Examples:

Container build:
- container registry
- upstream image registry

Git operations:
- source control provider

GitLab CI:
- GitLab API
- GitLab runner capabilities
- artifact/container registry

Kubernetes deployment:
- Kubernetes context
- namespace
- image registry
- secret mechanism

Node.js:
- npm registry

Python:
- PyPI index

Java:
- Maven repository

Do not request resources that are unrelated to the task.

### 2. Discover existing configuration

Inspect:

- .agent/resources.yaml
- environment variables
- git remotes
- Docker configuration
- kubeconfig
- package manager configuration
- CI configuration
- repository files

Prefer discovered information over asking the user.

### 3. Construct a resource requirement graph

For each resource classify:

- REQUIRED
- OPTIONAL
- RESOLVED
- MISSING
- INVALID

Include the reason the resource is required.

### 4. Ask only for missing information

Group missing resources into one question.

Ask for endpoints, namespaces, project identifiers, and credential
references.

Never request raw passwords or tokens when an environment variable,
credential helper, secret manager, or existing login can be used.

### 5. Validate resources

Perform low-impact connectivity checks.

Validation should distinguish:

- DNS
- TCP
- TLS
- protocol
- authentication
- authorization

Authentication-required responses may still indicate successful
connectivity.

Do not create, delete, push, or modify remote resources during
discovery unless explicitly required.

### 6. Retry intelligently

Do not blindly retry the same operation.

Classify the error and change the hypothesis.

Examples:

DNS failure
→ verify hostname

TLS failure
→ inspect CA configuration

401
→ endpoint is reachable; authentication required

403
→ authentication succeeded but authorization may be insufficient

404
→ verify API path or namespace

timeout
→ verify routing, VPN, proxy, or firewall

Limit discovery retries.

### 7. Persist the resource profile

Record validated endpoints in:

.agent/resources.yaml

Never store secrets directly.

Credential configuration should reference:

- environment variables
- credential helpers
- secret managers

### 8. Use resolved resources

Once a resource has been resolved, all subsequent operations must use
the resolved endpoint.

Do not silently fall back to public infrastructure.

If a required resource cannot be resolved, stop and explain what
information is missing.

## Container image resolution

Distinguish between:

1. private image destination
2. upstream image proxy/cache

A Harbor installation does not imply Docker Hub images are available.

Example:

docker.io/library/ubuntu:24.04

may resolve to:

registry.internal/dockerhub/library/ubuntu:24.04

only when the configured Harbor project is verified as a Docker Hub
proxy cache.

Never invent this mapping.

## Completion criteria

Resource setup is complete when:

- all required resources are resolved
- required endpoints are reachable
- authentication mechanisms are known
- resource mappings are persisted
- no unresolved default external endpoint is required by the plan

After setup completes, continue with the development task.
```

---

# 十二、但我建議再往前一步：不要只做 Skill

這是整個設計最重要的地方。

如果只有：

```text
SKILL.md
```

本質上還是：

```text
Please remember not to use docker.io.
```

模型還是可能忘。

你的 Harness 原則其實明確指出，重要 constraint 不應只依靠 model compliance，而應程式化驗證。

所以我會做成：

```text
Skill
+
Resource Resolver
+
Executable Guardrail
```

例如：

```text
tools/
└── resource
```

提供：

```bash
resource analyze
resource list
resource resolve
resource doctor
```

---

## `resource analyze`

例如：

```bash
$ resource analyze

Detected requirements:

RESOURCE              STATUS       SOURCE
gitlab                RESOLVED     git remote
container-registry    RESOLVED     resources.yaml
dockerhub-proxy       MISSING      Dockerfile
kubernetes            RESOLVED     kubeconfig
npm-registry          NOT_REQUIRED
```

---

## `resource doctor`

```bash
$ resource doctor

GitLab
  https://gitlab.internal
  DNS       PASS
  TLS       PASS
  HTTP      PASS

Harbor
  https://registry.internal
  DNS       PASS
  TLS       PASS
  Registry  PASS
  Auth      PASS

DockerHub Proxy
  registry.internal/dockerhub
  Registry  PASS

Kubernetes
  dev-cluster
  API       PASS
  Namespace PASS

Overall:
PASS
```

然後統一：

```bash
make setup
```

或者：

```bash
make doctor
```

---

# 十三、甚至可以做 Network Guardrail

未來還可以做到：

```text
Agent
 │
 ▼
resource resolver
 │
 ├── internal GitLab
 ├── Harbor
 ├── npm mirror
 ├── PyPI mirror
 └── Maven mirror
```

任何未知 endpoint：

```text
registry-1.docker.io
```

直接：

```text
RESOURCE_EXTERNAL_ENDPOINT_UNRESOLVED

Attempted:
  registry-1.docker.io

Reason:
  No approved resource mapping exists.

Required capability:
  Docker Hub image source

Action:
  Run:
    resource resolve dockerhub

or provide an approved upstream endpoint.
```

這就不是：

> 希望 Agent 記得不要連 Docker Hub。

而是：

> **Agent 根本無法在沒有 Resource Resolution 的情況下偷偷依賴 Docker Hub。**

這才真正符合 Harness Engineering。

---

# 十四、我會把整體架構定義成

```text
                  Development Plan
                         │
                         ▼
               ┌──────────────────┐
               │ Resource Analyzer│
               └────────┬─────────┘
                        │
                  requirements
                        │
                        ▼
               ┌──────────────────┐
               │Resource Resolver │
               └────────┬─────────┘
                        │
        ┌───────────────┼──────────────┐
        ▼               ▼              ▼
      GitLab          Harbor          K8s
        │               │              │
        └───────────────┼──────────────┘
                        │
                        ▼
                Resource Profile
                        │
                        ▼
                 resource doctor
                        │
                 PASS / feedback
                        │
                        ▼
                  Coding Agent
                        │
                        ▼
                   make verify
```

這讓你的 Harness 又多了一層：

```text
Intent
   ↓
Resource Discovery
   ↓
Environment Resolution
   ↓
Executable Guardrails
   ↓
Implementation
   ↓
Verification
   ↓
Feedback
```

我認為 **Resource Discovery / Environment Resolution 應該正式成為你 Agentic SDLC 的第一階段**。因為對企業內部 Agent 而言，「不知道環境其實已有 GitLab、Harbor、mirror、K8s、artifact repository」造成的浪費，往往不是模型能力問題，而是 **Harness 沒有把環境能力暴露給 Agent**。

而你文件最後定義的核心是工程化「Agent 與環境之間的 Feedback Loop」；這個 setup skill 實際上就是把 **環境本身也變成 Agent 可查詢、可驗證、可學習的介面**。

下一階段我會建議直接把它實作成 **`resource-bootstrap SKILL.md + resources.yaml schema + resource CLI + doctor command`**。如此不論你之後用 Codex、Claude Code、OpenCode 或其他 Agent，都只需要接同一個 Harness，而不是每個 Agent 各自學一次你的內部環境。
