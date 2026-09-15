# Python 匯入分支

建立 Python 靜態依賴 gate 時讀本文件。`ast.parse` 能識別語法，但不會自動解析 module 身分。
先從 repo 確認 package root、`__init__.py`、namespace package 與執行時搜尋路徑。
不要從這份範例推測真實專案一定以 `src` 為 package。

## 建立 fixture 矩陣

以下假設真實模組是 `src.domain.order` 與 `src.infrastructure.db`，且禁止 domain → infrastructure。
fixture 應建立相應檔案，並從 domain 模組發出 import。用專案實際名稱替換這些路徑。

| 語法分支 | 範例 | 結果 |
| --- | --- | --- |
| 直接 import | `import src.infrastructure.db` | 違規 |
| alias 與多目標 | `import os, src.infrastructure.db as store` | 違規，alias 不改變依賴身分 |
| from module | `from src.infrastructure.db import save` | 違規 |
| 從父 package 匯入子模組 | `from src import infrastructure` | 違規 |
| 父 package 加 alias | `from src import infrastructure as store` | 違規 |
| 相對 from | `from ..infrastructure import db` | 違規 |
| 相對父 package | `from .. import infrastructure` | 違規 |
| 巢狀 package | 在 `src/domain/nested/order.py` 使用 `from ...infrastructure import db` | 違規 |
| 合法相依 | `from src.domain import value` | 通過 |
| 文字與註解 | 字串或註解內含 `import src.infrastructure.db` | 通過 |
| 語法損壞 | `def unfinished(:` | 執行錯誤，非零 |
| 沒有預期原始碼 | domain 目錄不存在，或存在但沒有 `.py` | 執行錯誤，非零 |

所有分支都有真實結果後，才能宣稱涵蓋這些靜態匯入形式。
若只支持部分形式，明列限制並保持相關需求未完成；不得把漏檢當成通過。

## 解析要點

- `ast.Import`：檢查每個 `alias.name`，不是 `alias.asname`。
- `ast.ImportFrom`：同時檢查 `node.module` 與 `node.names`。
  `from src import infrastructure` 的 `node.module` 只有 `src`，子模組名稱在 `node.names`。
- 依真實 package/module 清單解析 `from P import x`：如果 `P.x` 是子模組，它也是依賴目標。
  如果 `x` 是 `P` 的一般屬性，相依目標是 `P`；不要捏造不存在的模組。
- 相對 import 以**目前檔案所屬 package** 與 `node.level` 計算。
  一般模組與 `__init__.py` 的 package 身分都要有樣本驗證。
- 目標 prefix 以模組邊界比對：`src.infrastructure` 或其 `.` 子模組。
  `src.infrastructure_helpers` 不等於 `src.infrastructure`。
- 決定是否將條件式或 `TYPE_CHECKING` 區塊內的靜態 import 納入規則，並記錄其來源依據。

`importlib.import_module`、`__import__`、修改 `sys.path`、re-export 或 plugin loading 可能引入其他相依。
需要這些能力時加入各自的解析規則與 fixture；單靠 AST import nodes 不代表完整執行期依賴圖。
