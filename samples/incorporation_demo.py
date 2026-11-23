"""断源核账演示：凭单导出、源账并入、离线供数与按原号领取封存。

复用 samples/incorporation_fixture.py 的两仓工单起点。甲仓首笔 +2 L 凭单
从 R1 并入源账（R2 正式源读 9 L），再普通更正到 R3 的 11 L。源仓关闭后，
甲仓逐版接纳、乙仓从 R1 跳收 R3，双仓现账应计 14、13 L；甲仓早班封存
仍按原成果号领得 7 L 与 +2 L，合计 9 L。
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.reconcile import reconcile_statement
from archive.retrieve import retrieve
from archive.seal import seal
from exchange.envelope import encode_piece
from exchange.issue import issue_v2
from ledger.accept import accept
from ledger.adjustments import configure_branch, export_voucher
from ledger.daily import read
from quality.disposition import QualityOperation
from quality.revisions import freeze, incorporate
from samples.incorporation_fixture import CORRECTION_ORIGIN, INCORPORATION_ORIGIN, prepare


def run_incorporation(directory: Path) -> dict:
    paths = {name: directory / f'{name}.sqlite3' for name in ('source', 'branch_a', 'branch_b')}
    with (closing(sqlite3.connect(paths['source'])) as source,
          closing(sqlite3.connect(paths['branch_a'])) as branch_a,
          closing(sqlite3.connect(paths['branch_b'])) as branch_b):
        day, credential, first, old_id, a_one, _ = prepare(source, branch_a, branch_b)
        configure_branch(branch_a, '甲仓')
        configure_branch(branch_b, '乙仓')
        voucher = export_voucher(branch_a, a_one.adjustment_id)
        middle = incorporate(source, day, first, credential, voucher, INCORPORATION_ORIGIN)
        final = freeze(source, day, middle, [QualityOperation(
            credential, 'correct', '末班复测', 11, CORRECTION_ORIGIN,
        )])
        step_payloads = [encode_piece(issue_v2(source, day, first, middle)),
                         encode_piece(issue_v2(source, day, middle, final))]
        skip_payload = encode_piece(issue_v2(source, day, first, final))
    # 源仓已断开：甲仓逐版接纳，乙仓从 R1 跳收 R3，随后各自封存。
    with (closing(sqlite3.connect(paths['branch_a'])) as branch_a,
          closing(sqlite3.connect(paths['branch_b'])) as branch_b):
        for payload in step_payloads:
            accept(branch_a, day, payload)
        accept(branch_b, day, skip_payload)
        book_a, book_b = read(branch_a, day), read(branch_b, day)
        new_ids = (seal(branch_a, day), seal(branch_b, day))
    with (closing(sqlite3.connect(paths['branch_a'])) as next_a,
          closing(sqlite3.connect(paths['branch_b'])) as next_b):
        old = retrieve(next_a, old_id)
        sealed = (retrieve(next_a, new_ids[0]), retrieve(next_b, new_ids[1]))
    checks = [reconcile_statement(statement) for statement in (old, *sealed)]
    if any(not check.matches for check in checks):
        raise ValueError('封存账复算不符')
    line_a, = book_a.lines
    line_b, = book_b.lines
    old_line, = old.lines
    return {
        'revision': final,
        'source_readings': [line_a.row.quantity, line_b.row.quantity],
        'totals': [book_a.total, book_b.total],
        'local_adjustments': [[item.delta for item in line_a.adjustments],
                              [item.delta for item in line_b.adjustments]],
        'incorporated': [[item.delta for item in line_a.incorporated],
                         [item.delta for item in line_b.incorporated]],
        'incorporated_from': [[item.branch_id for item in line_a.incorporated],
                              [item.branch_id for item in line_b.incorporated]],
        'old_statement': {'id': old_id, 'quantity': old_line.quantity,
                          'adjustments': [item.delta for item in old_line.adjustments],
                          'total': old.total},
        'new_statements': [{'id': number, 'total': statement.total}
                           for number, statement in zip(new_ids, sealed)],
        'reconciled': [check.total for check in checks],
    }


def main():
    with TemporaryDirectory(prefix='qingwan-incorporation-') as directory:
        result = run_incorporation(Path(directory))
    expected = {
        'revision': 3,
        'source_readings': [11, 11],
        'totals': [14, 13],
        'local_adjustments': [[3], [2]],
        'incorporated': [[2], [2]],
        'incorporated_from': [['甲仓'], ['甲仓']],
        'old_statement': {'id': 1, 'quantity': 7, 'adjustments': [2], 'total': 9},
        'reconciled': [9, 14, 13],
    }
    actual = {key: result[key] for key in expected}
    if actual != expected:
        raise AssertionError(f'断源核账结果与手算不符: {result!r}')
    print(f'断源后现账 甲 {result["totals"][0]} L、乙 {result["totals"][1]} L，'
          f'仍加计 甲{result["local_adjustments"][0]}、乙{result["local_adjustments"][1]}，'
          f'双仓可见甲仓并入凭单 {result["incorporated"][0]} L；'
          f'按原成果号 {result["old_statement"]["id"]} 领得封存 {result["old_statement"]["total"]} L，'
          f'新封存复算 {result["reconciled"][1:]} L')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v9.incorporation_offline',
        'input': {'original': 7, 'branch_deltas': {'甲仓': [2, 3], '乙仓': [2]},
                  'incorporated': '甲仓#1', 'corrected_reading': 11},
        'expected': expected, 'actual': actual,
        'code_paths': ['ledger/adjustments.py', 'quality/revisions.py', 'exchange/issue.py',
                       'ledger/accept.py', 'ledger/daily.py', 'archive/seal.py',
                       'archive/retrieve.py', 'archive/reconcile.py'],
        'oracle': '手算：源读 7 并入甲首笔 +2 得 9，再更正为 11（已含并入额）；'
                  '甲 11+3=14、乙 11+2=13；早班封存 7+2=9。',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
