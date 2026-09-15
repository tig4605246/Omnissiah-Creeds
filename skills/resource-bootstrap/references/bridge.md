# Bridge Mode

把既有專案的外部依賴來源改成使用者已提供的內部來源。
先理解消費者與來源類型，再選擇改寫方式；禁止從 hostname 相似性推測映射。

## 1. 盤點來源

先確認任務是「評估／產生 plan」或「執行遷移」。只有後者包含 apply。
讀 repo 指引與相關建置設定，執行：

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge analyze
```

先讀 [格式與涵蓋範圍](bridge-formats.md)。來源包含明確 URL、短映像名與隱含的 package transport。
analyze 的候選有來源位置、種類、消費方式；不把所有文字中的 URL 當成依賴。
另檢查會影響實際執行的環境變數、套件設定、Git config、CI、容器階段與安裝腳本。

**完成條件：** 本次要執行的每條下載／安裝／建置路徑，都有來源紀錄或明確的 coverage 缺口。

## 2. 建立有證據的 mapping

沿用 setup 的 resource profile，建立 `.agent/bridge.json`；格式見
[bridge.example.json](../assets/bridge.example.json)。只存端點、完整來源與 evidence，不存秘密。

| 類型 | 處理方式 |
| --- | --- |
| Container | 使用 resource profile 內已確認的 proxy project；保留 tag／digest |
| Package transport | 使用 native registry／proxy 設定；保留 package／module 名稱與版本 |
| Git／submodule | 精確 repository URL 對應到已確認的 mirror repository |
| Artifact／lock tarball | 精確 artifact-to-artifact URL 映射與 integrity；不做 hostname 批次替換 |

不足的資訊一次詢問；已有使用者授權及確認來源直接沿用。
Bridge 使用的 resource profile 不允許 `direct_registries` fallback。
若 setup profile 原本允許公共 registry，保留原設定；另選一份 bridge 用的完整 profile，明確記錄選擇。

有連線授權與環境時，在 apply 前查詢精確 target 的 metadata、artifact integrity 與使用權限。
使用者明確要求離線準備時可以依提供的映射產生與套用計畫，但 target 存在性與能力保持未驗證。
沒有證據的必要 target 不能靠 `confirmed_proxy: true` 或手寫「pass」解決。

**完成條件：** 每個映射有來源依據；target 的實證或尚未驗證狀態已清楚記錄。

## 3. 產生並檢查 plan

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge plan
```

預設只輸出 JSON，完全不改檔。檢查 sources、findings、changes 的精確位置與替換值。
plan 不保存整份原始程式碼，避免把無關資料抄入計畫。
未知 mapping、動態來源、unsupported adapter 或 checksum 衝突會阻擋 apply。

不支持的格式先讀 [生態系統處理方式](bridge-ecosystems.md)。
任務包含實作時，可按實際專案補一個標準函式庫或既有 native tool 的窄範圍 adapter，並建立正反案例。
保留不支持的 finding，直到有可靠解析及驗證；不要刪除 source、移除檢查或手工修改 plan 來通過。
需要額外服務、權限或無法確定語意時，完成獨立工作後明列阻礙。

對已授權的遷移，確認 plan 符合原意後保存：

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge plan --save
```

這會寫入 `.agent/bridge.plan.json`。`--save` 是明確的 metadata 寫入操作。
不要為已授權的正常遷移再增加一輪固定核准；只有缺少會影響內容或權限的選擇才詢問。

**完成條件：** plan 沒有 unresolved／unsupported finding，且每項修改都能對應本次來源轉換。

## 4. Apply 與恢復

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge apply
```

工具會重新計算 plan；repo、設定、resource profile 或 saved plan 改變時拒絕套用。
檔案更新保留原權限，使用逐檔原子替換；寫入失敗時嘗試回復已由本次修改且仍未變動的檔案。
這是有恢復機制的檔案交易，不是跨檔案的 OS 原子交易；若回復遇到新變更，保留備份並回報衝突。

apply 產生 `.agent/bridge.lock.json`，記錄 original／resolved 來源、transaction ID、設定雜湊與未執行的 runtime 狀態。
原始檔案備份位於 `.agent/bridge-backups/<transaction>/`；備份保留在本機，不加入版本控制。
工具會補上備份的 ignore 規則，保留原本未涉及的檔案及設定。

備份不是原始目錄結構。先用下列命令讀 manifest，按 `files[].path` 對應 `backup_file`，再比較內容：

```sh
python3 -m json.tool <repo>/.agent/bridge-backups/<transaction>/manifest.json
```

例如 `Dockerfile` 的備份可能是 `files/0001.orig`；新建檔案的 `backup_file` 是 null。

查看最終 diff，確認 dependency identity、versions、integrity、shell 行為與 build stage 沒有被意外改變。
需要撤回此次 bridge，且使用者已要求還原或本次失敗處理需要回復自己的修改時，執行：

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge restore --transaction <apply輸出的ID>
```

restore 會先檢查備份、metadata 與目前檔案；目前內容有新變更時拒絕覆蓋。
還原會移除本次新建的確切檔案並恢復原檔；備份仍保留。交付時說明還原範圍。

**完成條件：** 預期替換已落在實際消費者設定；diff 與 plan 一致；lock 與恢復資訊可讀。

## 5. 重新掃描與隔離驗證

```sh
python3 <skill-dir>/scripts/resource.py --root <repo> bridge verify --static
```

這會重建支援範圍內的 source graph。`static_pass` 只表示這些靜態來源已橋接。
不加 `--static` 時，離線 CLI 會以非零結果明列 runtime 未驗證，避免把部分結果當成全部完成。

接著按 [隔離執行與 network witness](bridge-runtime.md) 使用 host 真正提供的環境控制來驗證。
不能用空的 observed endpoints、暖快取成功、一次 HTTP 200，或未執行的命令宣稱無外部依賴。

每個根因預設最多修正 3 輪；相同結果連續出現 2 次時先取得新證據。
修正後重新 plan／apply／verify，保留規則與 mapping 來源依據。
本機靜態 gate 可接入 `make verify`；隔離 restore/build/test 使用單獨的明確入口與 witness。

**完成條件：** 支持的靜態來源通過，且本次所需的隔離執行實際成功；否則交付「離線橋接完成／runtime 未驗證」與確切缺口。
