# 分仓调整并入源账

原有初始化、普通质控、供数、日账和封存入口见 README.md。源仓与每个分仓使用独立连接和数据库文件。下列写接口也在空闲连接上调用，并管理各自数据库的事务。

## 新增调用

- `ledger.adjustments.configure_branch(conn, branch_id)` 返回配置成功的仓号。同号可重复设置，已配置的仓号禁止改绑。仓号按既有非空名称规则校验，由业务预先分配，每个独立分仓一个唯一仓号。重开沿用已设仓号，也可给已有 V1 调整记录的分仓配置。
- `ledger.adjustments.export_voucher(conn, adjustment_id)` 按本地调整号读取，返回不可变的 `AdjustmentVoucher`，字段依次为 `branch_id`、`adjustment_id`、`credential`、`delta`、`origin`。`credential` 为原 `SampleCredential`，`origin` 为调整记录的 `ReadingOrigin`，`delta` 沿用有符号精确整数。配置错误、未配置或未知调整号引发 `AdjustmentRejected`。导出可重领，历史登记可导出。导出不改变计量。
- `quality.revisions.incorporate(conn, station_day, base, credential, voucher, origin)` 返回新冻结修订号。`origin` 是这次源账并入的 `ReadingOrigin`。`credential` 必须是凭单所指的同一原样，仍属当前修订的可计行。并入把凭单差额加到这条原样的有效读值，采用本次并入出处，且同版冻结凭单。按仓号和原调整号判断是否已并入。错样、错版、非法凭单及重复并入引发 `InvalidQualityOperation`，无新修订。凭单由业务可信渠道送达源仓。
- `exchange.issue.issue_v2(conn, station_day, base, target)` 发行含并入口径的固定供数对象，同用发行号空间。同站日的起止修订和 `IssueRejected` 沿用 `issue` 约定。对象可经 `exchange.envelope.encode_piece` 转成来件字节，原 `read_issued_piece` 可按发行号复领并重新编码为原字节。V2 正文和证据格式由实现确定。原 `issue` 可继续发行目标无并入事实的 V1 件，含并入事实的目标必须拒绝经该路径发行。
- `ledger.accept.accept(conn, station_day, supplied)` 继续接纳 V1，也接纳 V2 对象或其 JSON 字节。接纳 V2 须使用已配置的分仓。来件需证明目标修订的并入口径，并与本仓已经采用的确切底账相符，底账比对也须区分并入事实。V1 在用账的并入依据为空。错误站日、底账不符、损坏或错绑的来件，以及本仓凭单与原调整记录不符，均引发 `AcceptanceError`。另一仓的凭单在本仓可以没有对应登记。旧修订、读值、资格与并入依据在失败时一同保留。供数由业务可信渠道交付，校验范围为误损、错件、错底和不一致的原记录。

## 计量与领取

并入后，同一原样的 `correct` 或带新读值的 `release` 输入为已含并入额的正式读值。原样暂扣期间不出计量行，解除暂扣沿用该原样的并入事实。撤样后的同号补采有独立原样身份。已冻结的凭单事实仍归原样。

`ledger.daily.read` 和 `archive.retrieve.retrieve` 继续返回各自原有结果。每条 `DailyLine` 和 `ClosedLine` 额外提供 `incorporated`，即本行正式源读所含的 `AdjustmentVoucher` 元组，旧账无并入时为空。仍从 `adjustments` 领取实际在本地加计的各笔调整，按原有本地顺序。源读与其出处仍从原字段领取。新封存固定当时这些字段，重开后按原成果号读取并独立复算。`read_adjustments` 仍返回完整的本地原调整历史，包括已经并入源读的记录。

## 两仓工单

`samples/incorporation_fixture.py` 复用现有样例的原读和核账单。两座独立分仓在 R1 各有本地第 1 笔 +2 L，原样凭据、差额和出处完全一样。甲仓先封存 9 L，再登记 +3 L。用甲仓第 1 笔凭单从 R1 并入，R2 的正式源读为 9 L，出处为夹具的 `INCORPORATION_ORIGIN`。再用 `CORRECTION_ORIGIN` 更正到 R3 的 11 L。关闭源仓后，逐版接纳和从 R1 跳收 R3 均应得到甲 14 L、乙 13 L。甲在末版仅加计 +3 L，乙仍加计自己的 +2 L，两仓均可看到甲的并入凭单。甲原封存仍可领得 7 L 和 +2 L，合计 9 L。

原样直接普通更正为相同读值和出处时，没有并入凭单，末版应计甲 16 L、乙 13 L。两座独立源库可各自形成 R2 的 9 L 和相同出处，一座用了普通更正，另一座用了并入。后者的 R2→R3 来件必须拒绝采用前者 R2 的分仓，即使旧库存摘要相同。仓端提交失败可复用 `tests/support.py` 的 `arm_commit_failure` 在换账时制造延迟外键错误，并用 `allow_commit` 后重试。

## 运行

保留 `.pointwise-runtime.json` 的现有运行约定。沿 `check_project.py` 核验原回归和新增双仓链路，失败须返回非零。演示入口展示断源后双仓现账及按原号领取的 9 L 封存。原 `python3 -B -m samples.demo` 继续演示原流程。

直接运行 `python3 -B -m samples.incorporation_fixture` 可查看 V1 普通更正对照，输出相同的两笔本地调整、正式源读 11 L、甲乙 16/13 L 及原封存 9 L。这个夹具也可通过 `prepare(source, branch_a, branch_b)` 建立工单起点，返回站日、原样凭据、初版、甲早班成果号、甲首笔及乙首笔原调整。
