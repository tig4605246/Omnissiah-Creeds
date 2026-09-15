# Harness Engineering / Agentic SDLC 核心論述與實作準則

## 核心論述

Harness Engineering 的核心，不是試圖透過更長、更詳細的 Prompt，讓 AI Agent 記住所有軟體工程規範，而是建立一套**能持續約束、驗證並修正 Agent 行為的工程環境**。

在傳統軟體開發中，開發者透過 Coding Convention、Architecture Document、Code Review、CI/CD 等方式維持程式品質；進入 Agentic SDLC 後，這些機制仍然存在，但需要進一步轉化成 Agent 可以直接觀察、執行與回應的機器可讀介面。

因此，一個成熟的 Agentic SDLC 不應只依賴：

> 「請遵守 Hexagonal Architecture。」

而應建立：

> 「如果 Domain Layer 依賴 Infrastructure Layer，驗證程式必須失敗，並明確告訴 Agent 違反了什麼規則，以及應該朝什麼方向修正。」

也就是將過去存在於文件、團隊默契與人工 Code Review 中的關鍵工程約束，逐步轉化為 **Executable Guardrails**。

Agent 的工作流程因此形成持續閉環：

**理解意圖 → 修改程式 → 執行驗證 → 取得 Feedback → 自我修正 → 再次驗證 → 通過後交付**

這種模式的重點不是期待 Agent「永遠做對」，而是建立一個即使 Agent 做錯，也能快速偵測、提供可操作回饋並使其自行收斂的系統。

因此，Harness Engineering 的真正目標是：

**不要只提升模型第一次產生正確答案的機率，而要提升整個系統最終收斂到正確結果的機率。**

---

# 一、Prompt 負責表達意圖，Guardrail 負責維護邊界

Prompt、`AGENTS.md`、Architecture Document 等文件主要負責描述：

- 系統希望採取什麼設計方向
- 專案有哪些 Coding Convention
- 開發流程應如何進行
- 什麼樣的實作通常較為理想
- Agent 完成工作前應執行哪些驗證

但對於不可妥協的規則，不應只依賴自然語言。

例如：

- Domain 不可依賴 Infrastructure
- HTTP Handler 不可直接存取 Database
- UI Component 不可直接呼叫 Data Access Layer
- 不允許 Circular Dependency
- API Schema 不可產生未經允許的 Breaking Change
- Production Workload 必須符合安全性政策

這些規則應盡可能轉化成：

- Architecture Test
- Static Analysis
- Dependency Analysis
- Contract Test
- Policy as Code
- Schema Validation
- Security Scanner
- CI Gate

核心原則為：

> **文件描述期待，程式驗證底線。**

---

# 二、Guardrail 必須是可執行且具有決定性的

有效的 Guardrail 必須具備三個特性：

### 1. Deterministic

相同輸入應得到相同結果，不應依賴模糊的 LLM Judgment。

例如：

```text
domain → infrastructure
```

若規則禁止此依賴，就應永遠判定失敗。

### 2. Machine Executable

Guardrail 必須能直接透過 Command 執行，例如：

```bash
make verify
```

並使用標準 Exit Code：

```text
0 = PASS
non-zero = FAIL
```

如此 Agent、Developer 與 CI 才能使用相同驗證機制。

### 3. Actionable

失敗訊息不能只說：

```text
Architecture check failed.
```

而應提供足以讓 Agent自行修正的資訊，例如：

```text
ARCH001: Domain depends on Infrastructure

Source:
  internal/domain/order/service.go

Forbidden dependency:
  internal/infrastructure/postgres

Expected direction:
  infrastructure -> ports -> domain

Suggested repair:
  Introduce a repository interface and inject its implementation.
```

Guardrail 的錯誤訊息本身，就是 Agent 下一輪推理的重要 Context。

因此：

> **Guardrail 不只是檢查器，也是 Feedback API。**

---

# 三、建立 Self-Correcting Loop，而不是追求 One-Shot Generation

Agentic SDLC 不應假設 Agent 第一次實作就完全正確。

更可靠的工作模式是：

```text
Task
 ↓
Agent reasoning
 ↓
Code modification
 ↓
Verification
 ↓
PASS ─────────────→ Finish
 │
 FAIL
 ↓
Structured feedback
 ↓
Agent repair
 ↓
Verification
 ↓
...
```

因此 Agent 是否第一次犯錯不是最重要的問題。

真正需要評估的是：

- 錯誤是否能被偵測？
- Feedback 是否足夠明確？
- Agent 是否能根據 Feedback 修正？
- 系統是否能在有限 iteration 內收斂？

這代表 Agentic Software Engineering 的品質指標應從：

**First-shot correctness**

逐步轉向：

**Convergence reliability**

---

# 四、建立單一 Verification Entry Point

Agent 不應需要記住十幾條驗證 Command。

專案應提供統一入口，例如：

```bash
make verify
```

其內部可以包含：

```text
compile
lint
unit test
architecture test
dependency check
security check
contract test
integration test
```

例如：

```text
make verify
 ├─ compile
 ├─ lint
 ├─ unit-test
 ├─ architecture
 ├─ dependency
 ├─ security
 └─ contract
```

Agent 的工作指示因此只需要：

> 完成修改後執行 `make verify`；若失敗，分析錯誤並修正，直到所有驗證成功。

這可以大幅降低 Agent 的認知負擔，也避免不同 Agent 使用不同驗證流程。

---

# 五、Developer、Agent 與 CI 必須共用同一套 Guardrail

不可建立：

```text
Agent checks
Developer checks
CI checks
```

三套彼此不同的規則。

理想模式是：

```text
Developer ─┐
           │
Agent ─────┼──→ make verify
           │
CI ────────┘
```

同一套 Verification Harness 應同時服務：

- Local development
- Coding Agent
- Pull Request
- CI/CD pipeline

如此才能避免：

> Agent 認為工作完成，但 CI 使用另一套邏輯判定失敗。

因此：

> **Local truth 必須等於 CI truth。**

---

# 六、把 Architecture 從 Documentation 轉化為 Architecture as Code

架構文件仍然重要，但不可只存在於文字描述。

重要的 Architecture Invariant 應逐步程式化。

例如：

```text
Domain cannot depend on Infrastructure
```

轉化為：

```text
dependency rule
```

或：

```text
HTTP handler cannot access repository directly
```

轉化為：

```text
AST / import graph rule
```

這與現代基礎設施工程的演進相同：

```text
Wiki
 ↓
Infrastructure as Code
 ↓
Policy as Code
```

Agentic SDLC 則進一步演進為：

```text
Architecture Document
 ↓
Architecture as Code
 ↓
Executable Guardrails
```

---

# 七、Guardrail 應限制解空間，而不是替 Agent 決定所有實作

Guardrail 的目標不是將所有設計決策硬編碼。

適合 Guardrail 的通常是：

- 明確
- 可驗證
- 不應違反
- 違反後容易造成長期系統性問題

例如：

```text
禁止 Domain → Infrastructure
禁止 Circular Dependency
禁止 Breaking API Change
禁止敏感資訊進入 Repository
```

但以下原則通常仍應保留給 Agent 與 Human Judgment：

```text
命名是否清晰
抽象程度是否合理
是否過度工程
API 是否直觀
Domain Model 是否優雅
```

因此：

> **Harness 應建立安全邊界，而不是消滅 Agent 的設計自由。**

---

# 八、允許 Exception，但 Exception 本身也必須可治理

大型系統一定會存在合理例外。

但例外不能只是：

```text
// TODO temporary exception
```

應將例外明確建模，例如：

```yaml
rule: ARCH001
path: internal/domain/legacy
reason: Legacy migration
owner: platform-team
expires: 2026-10-31
```

Exception 至少應包含：

- Rule
- Scope
- Reason
- Owner
- Expiration

Guardrail 必須自動檢查其有效性。

因此：

> **例外不是繞過政策，而是政策的一部分。**

---

# 九、Harness 本身也是正式軟體產品

Harness 不應被視為零散 Shell Script 的集合。

隨著 Agent 自主程度提高，Harness 會逐漸承擔：

- Coding policy
- Architecture policy
- Testing policy
- Security policy
- Dependency policy
- Release policy
- Agent feedback

因此 Harness 本身應具備：

- Version Control
- Automated Test
- Clear Ownership
- Stable Interface
- Versioning
- Observability

尤其錯誤訊息格式最好具有穩定結構，例如：

```text
RULE_ID
SOURCE
VIOLATION
EXPECTED
SUGGESTED_FIX
```

因為未來 Consumer 不只會是人類，也會是 Agent。

---

# 十、把 Agent 視為受工程系統約束的執行者

Agent 不應被當作完全可信任的 autonomous developer。

也不應只是被視為 autocomplete。

更好的模型是：

> **Agent 是具有推理與執行能力，但必須在明確工程邊界中運作的 Software Engineer。**

它可以自行：

- 探索 Repository
- 修改程式
- 執行測試
- 分析錯誤
- Refactor
- Retry

但是否能交付，則由 Harness 決定。

因此：

```text
Agent proposes.
Harness verifies.
CI enforces.
Human governs.
```

這四者各自負責不同層面的可靠性。

---

# 十一、Harness Engineering 的三層模型

一個成熟的 Agentic SDLC 可以分為三層：

## Level 1 — Intent

描述「我們希望系統如何被設計」。

例如：

```text
AGENTS.md
ARCHITECTURE.md
README
ADR
Engineering Guidelines
```

主要 Consumer：

```text
Human + Agent
```

---

## Level 2 — Executable Policy

描述「哪些事情不能發生」。

例如：

```text
Architecture Test
Dependency Rules
OPA
Conftest
Kyverno
Static Analysis
Schema Validation
```

主要 Consumer：

```text
Agent + Developer + CI
```

---

## Level 3 — Verification

確認「實際產生的系統是否正確」。

例如：

```text
Unit Test
Integration Test
Contract Test
E2E Test
Security Test
Performance Test
```

三層共同形成：

```text
Intent
   ↓
Executable Constraints
   ↓
Verification
   ↓
Feedback
   ↓
Self-correction
```

---

# 十二、Agentic SDLC 的核心設計準則

在本專案中，Harness Engineering 應遵循以下原則：

1. **Prefer executable rules over textual reminders.**  
   能以程式驗證的規則，就不要只存在於 Prompt。

2. **Make failure actionable.**  
   每個失敗應盡可能指出原因、位置與修正方向。

3. **Optimize for convergence, not perfection.**  
   系統目標不是讓 Agent 永不犯錯，而是讓錯誤可偵測、可修正並快速收斂。

4. **Use one verification interface.**  
   Agent、Developer 與 CI 應透過相同入口驗證系統。

5. **Keep CI and local behavior identical.**  
   不建立隱藏於 CI 的特殊規則。

6. **Encode architectural invariants.**  
   對長期架構品質重要的邊界應逐步轉化為 Architecture as Code。

7. **Guard boundaries, not creativity.**  
   Guardrail 約束不可接受的結果，不應替 Agent 決定所有設計細節。

8. **Treat exceptions as governed objects.**  
   所有例外都必須具有理由、Owner、Scope 與生命週期。

9. **Design machine-readable feedback.**  
   工具輸出除了方便人類閱讀，也必須方便 Agent 理解與採取行動。

10. **Treat the harness as production software.**  
    Harness 本身也需要測試、版本控制、Owner 與演進策略。

11. **Never rely solely on model compliance for critical constraints.**  
    Security、Architecture、Compatibility 等重要邊界應由程式驗證。

12. **Increase autonomy only when observability and verification increase with it.**  
    Agent 能執行越多自主操作，Harness 就必須提供越完整的檢查與 Feedback。

---

# 最終原則

Harness Engineering 的重點並不是打造一個「永遠不犯錯的 AI」。

而是打造一個：

> **即使 AI 犯錯，也能偵測錯誤、理解錯誤、修正錯誤，並在明確工程邊界內持續收斂的軟體開發系統。**

Agentic SDLC 的競爭力，因此不只取決於模型本身有多聰明，也取決於模型周圍的 Harness 有多完整。

最終我們真正要工程化的，不只是 Agent 的 Prompt，而是：

**Agent 與環境之間的 Feedback Loop。**