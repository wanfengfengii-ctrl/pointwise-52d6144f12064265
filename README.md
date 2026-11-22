# 清湾水质资料湖

清湾水质资料中心按站日实收采样量。站务员核对原单，质控员冻结放行、暂扣、更正和撤样，区级分仓值班员按班接纳供数并登记核账调整。次班在源仓断开后凭成果号核对交班结果。

## 运行

在项目根目录使用 Python 3.11 和标准库。

```sh
python3 -B check_project.py
python3 -B -m samples.demo
```

正常回归入口随运行环境提供。样例从 `samples/batch.json` 和 `samples/shifts.json` 读取固定数据，在独立临时目录建立两库，打印实际日账与封存复算结果。可另行执行同明细班次对照。

```sh
python3 -B -m unittest tests.test_handoff.HandoffTests.test_equal_projection
```

## 一天的业务

1. 站务员交付具名站日批、原序、有出处的整数读数，领取原批回执。
2. 质控员按当前修订冻结具名放行，原样 K 的读数为 7 L。
3. 源仓从 R0 向目标修订发行固定件，分仓接纳后读到 7 L。
4. 核账员凭原样凭据登记 +2 L，值班员封存 9 L 并记录成果号。
5. 源仓冻结暂扣，分仓接纳后读到空账。源仓也可正式撤样，并在随后实收同号补采。
6. 源仓关闭后，分仓重开，暂扣班次按原样凭据继续登记 +1 L 并封存空账。撤样班次的原样凭据失去登记资格。
7. 同一原样解除暂扣并更正为 11 L 后，暂扣班次计 14 L。同号补采若获准计量，则只按其自身原读与自有调整计量。
8. 次班按早班成果号领取 7 L、原核账单 +2 L 及其出处，独立复算 9 L。

## Python 接口

源仓连接先依次调用 `intake.batch_store.initialize_batch_store`、`quality.snapshots.initialize_revision_store`、`exchange.issued_store.initialize_issued_store`。分仓连接调用 `ledger.book_store.initialize_book_store` 和 `archive.statement_store.initialize_statement_store`。初始化可重复执行，保留库中已提交的业务。对同一源仓或分仓文件重开连接后可续办。每个写入口自行管理本库事务，调用时连接须处于空闲事务状态。两个角色使用各自的 SQLite 文件。

| 入口 | 输入与成功结果 | 业务错误 |
| --- | --- | --- |
| `intake.batches.ingest(conn, station_day, name, samples)` | `StationDay(站名, YYYY-MM-DD)` 和有序 `SampleInput(编号, 整数, 'L', ReadingOrigin(单名, 行号))`，返回 `BatchReceipt` | `BatchValidationError`，原批不落库 |
| `quality.revisions.freeze(conn, station_day, base, operations)` | 当前修订和有序具名 `QualityOperation`，返回整数后继修订 | `InvalidQualityOperation`，无新修订 |
| `exchange.issue.issue(conn, station_day, base, target)` | 同站日已冻结的起点与后继目标，返回含发行号的固定 `SupplyPiece` | `IssueRejected`，无新发行 |
| `ledger.accept.accept(conn, station_day, supplied)` | 供数件对象或其 JSON 字节，返回在用目标修订 | `AcceptanceError`，旧修订、可计行和资格继续在用 |
| `ledger.adjustments.adjust(conn, credential, delta, origin)` | 原样凭据、有符号整数差额和核账单行，返回 `Adjustment` | `AdjustmentRejected`，无新登记 |
| `ledger.daily.read(conn, station_day)` | `DailyBook` 带修订、有序可计行、逐条调整和合计，无在用账返回 `None` | 非法站日引发 `ValueError` |
| `archive.seal.seal(conn, station_day)` | 返回提交成功的日账成果号，空账可封存 | `SealRejected`，无新成果号 |
| `archive.retrieve.retrieve(conn, statement_id)` | 按号返回固定 `ClosedStatement` | `StatementNotFound` |

`read_batch` 按站日及批名复读原始行，`read_snapshot` 按整数修订读冻结处置及当版操作，`read_issued_piece` 按发行号复读固定件。`read_adjustments` 按原样凭据读本地历史登记，`reconcile_statement` 从封存值复算每行与全日结果。其定义分别位于 `intake/batch_store.py`、`quality/snapshots.py`、`exchange/issued_store.py`、`ledger/adjustments.py` 和 `archive/reconcile.py`。

首次成功实收建立站日的空起点 0。送审的实收样本先 `approve`，可计样本可 `hold`，暂扣样可 `release`。`correct` 适用于可计或暂扣原样，提供新读数和更正出处，`release` 也可携带更正。`withdraw` 正式结束已判定原样的资格并释放人工编号。每项操作均携带具名处置记录，同版按提交顺序应用。补采先等待撤样冻结，再独立实收。对照用例让两个班次接收源修订 1、3、4，两班都跳过源修订 2，随后另测从 1 直达 4。

## 计量与交接

按站日内批序及批内原序呈现可计行。每行以当版有效原读加上同一原样的所有已登记差额。读数与差额统一为整升，零是有效数据。原读出处、更正出处及核账单行分别保留。已接纳空账带修订，明细为空且合计为零。封存包含实际入账的逐条调整，领取只按成果号定位。

`encode_piece` 与 `decode_piece` 传输固定版本的 JSON 表示。件头绑定站日与两个修订，件体含目标可计行及原样资格增减，起点和目标摘要绑定各自的存续清册与可计行。分仓用自己的在用起点复算交接，资格和读数一起生效。暂扣资格信息携带原样凭据，暂扣原读留在源仓。件体与 SQLite 持久记录将数量表示为规范十六进制文本，Python API 保持精确整数，汇总也在 Python 中完成。

回归覆盖整批拒收、冻结历史、相同计量投影下的离线调整、逐版和跳版结果、非法件、真实 SQLite 提交回退、旧封存领取和已有数据的重复初始化。
