# Omnissiag-Creeds
Machine God who rules the machines with these creeds

## Harness Engineering skill

[`skills/harness-engineering/SKILL.md`](skills/harness-engineering/SKILL.md)
將 [`harness-skill-design.md`](harness-skill-design.md) 轉成可以執行的 agent 工作流程。

- 明確步驟、完成條件、範例與有限次修正，減少模型需要自行推導的流程。
- Python 3.9+ 標準函式庫 runner：單一驗證入口、JSON 回饋、timeout 與錯誤退出碼。
- 例外 helper：驗證 rule、檔案 scope、reason、owner、expires；只豁免指定 finding。
- 不需要 `pip`、`uv`、`npm` 或 `go get`。專案本身的檢查工具須已存在。

### Codex / OpenCode 共用

將整個 `skills/harness-engineering/` 複製到目標專案的
`.agents/skills/harness-engineering/`。保留 `assets`、`scripts`、`references`。
若目標已有同名 skill，先比較內容再更新。

```text
<target-repo>/
  .agents/skills/harness-engineering/
    SKILL.md
    assets/
    references/
    scripts/
```

共用目錄與基本 `name` / `description` 格式依
[Codex 官方說明](https://learn.chatgpt.com/docs/build-skills) 與
[OpenCode 官方說明](https://opencode.ai/v2/docs/skills)。
本 repo 的 `skills/` 是套件來源目錄，不會自動成為 host 的 skill 搜尋目錄。

載入後可用自然語言呼叫：

> 使用 harness-engineering，把這個 repo 的架構規則轉成可執行 guardrail，沿用既有驗證入口，只用標準函式庫。

Codex 也可用 `$harness-engineering`。若尚未安裝，可以明確要求 agent 讀取上述 `SKILL.md` 後執行。

### 驗證附件

在此 repo 根目錄執行：

```sh
python3 -I -S skills/harness-engineering/scripts/test_verify.py
python3 -I -S skills/harness-engineering/scripts/test_exceptions.py
```

`-I -S` 隔離環境並略過 site packages；附件不依賴第三方 Python 套件。
runner 是命令執行器；具體架構、安全與合約規則由 skill 引導 agent 按專案實作。
模型行為的試跑方式見 [evaluation.md](skills/harness-engineering/references/evaluation.md)。
附件共 18 項測試通過。另以 GPT-5.6 terra 試跑小專案，依首次漏檢補強指引後通過 13 項專案測試；
詳細範圍與限制記錄於同一文件，Claude Sonnet-5 尚未實測。
