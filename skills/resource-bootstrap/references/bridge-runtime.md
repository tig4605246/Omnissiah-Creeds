# 隔離執行與 Network Witness

Bridge CLI 本身沒有網路隔離或流量攔截能力，也不執行 package restore／build。
它無法證明任意 process 沒有碰到公共端點。完整遷移驗證使用 host 已提供的真實控制與證據。

## 執行前

1. 先通過 `bridge verify --static`，保留 config、lock 與 source snapshot。
2. 確認本次要跑的 restore、build、test、平台／architecture 及 native 工具版本。
3. 找到可用的 sandbox／container network／CI egress allowlist，確認它涵蓋整個 process tree、DNS、proxy、build daemon 與 BuildKit worker。
4. 用可回復的隔離工作目錄與新的測試 cache；保留使用者原本 cache。暖快取只能證明已快取的路徑。
5. 確認 internal targets、registry auth service、CA 與需要的 secret references；不將值寫入 log。

找不到 enforceable egress control 或沒有操作權限時，交付靜態結果及缺少條件。
不要以設定 HTTP proxy、指定環境變數或未觀測到請求，推論所有外部連線都已被阻止。

## 執行與紀錄

使用已授權的工具在隔離環境執行本次必要命令。
保留真實的命令、exit code、工具版本、平台、cache 狀態、使用的網路政策與 policy／witness log 路徑。
成功與被阻擋的連線都要保存：靜默 fallback 被擋後仍 build 成功，也代表存在未處理的外部嘗試。

紀錄表可包含以下欄位，內容只能由實際工具結果填入：

```text
Source snapshot:
Bridge config / lock:
Platform / tool versions:
Enforced network policy / evidence:
Restore command / exit code:
Build command / exit code:
Test command / exit code:
Cache state:
Allowed endpoints:
Observed / blocked destinations:
Witness log paths:
Unexercised branches / remaining limits:
```

這是紀錄模板，不是可以手動填上 pass 的認證格式。
要將既有 host log 轉成 machine-readable report，可寫標準函式庫 adapter，但它必須讀真實 log 並驗證對應 snapshot。
不接受由 agent 自行生成的「observed=[]」作為獨立證據。

## 結果界線

- `bridge verify --static` exit 0：只表示目前支持的靜態來源已橋接。
- restore/build/test 成功：只表示那些實際執行的命令成功。
- 以上成功，加上可驗證的 egress enforcement／network witness：支持該次平台、輸入、cache 條件與執行路徑的隔離運作結果。

即使完整試跑通過，仍列出未涵蓋的 optional dependencies、plugins、平台與動態路徑。
source、mapping、工具或必要環境改變後，舊 witness 不可當成本次結果。

離線 CLI 的完整 `bridge verify` 會明列 runtime 未執行並返回非零。
可把 `bridge verify --static` 接入 `make verify`，再用 repo 的原生隔離入口，例如 `make verify-bridge-runtime`，完成並保存上述證據。
