import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.reconcile import reconcile_statement
from archive.retrieve import retrieve
from archive.seal import seal
from archive.statement_store import initialize_statement_store
from exchange.envelope import encode_piece
from exchange.issue import issue
from exchange.issued_store import initialize_issued_store
from intake.batch_store import initialize_batch_store
from intake.batches import ingest
from intake.observations import SampleInput, StationDay
from intake.origins import ReadingOrigin
from ledger.accept import accept
from ledger.adjustments import adjust
from ledger.book_store import initialize_book_store
from ledger.daily import read
from quality.disposition import QualityOperation
from quality.revisions import freeze
from quality.snapshots import initialize_revision_store

SAMPLE_DIR = Path(__file__).parent


def _inputs():
    with (SAMPLE_DIR / 'batch.json').open(encoding='utf-8') as source:
        batch = json.load(source)
    with (SAMPLE_DIR / 'shifts.json').open(encoding='utf-8') as source:
        shifts = json.load(source)
    return batch, shifts


def _line(statement):
    line, = statement.lines
    return [line.quantity, line.origin.document,
            [[record.delta, record.origin.document] for record in line.adjustments]]


def run_workday(directory: Path) -> dict:
    batch, shifts = _inputs()
    station_day = StationDay(batch['station'], batch['day'])
    readings = [SampleInput(item['number'], item['quantity'], item['unit'], ReadingOrigin(*item['origin']))
                for item in batch['samples']]
    totals = []
    branch_path = directory / 'branch.sqlite3'
    with closing(sqlite3.connect(directory / 'source.sqlite3')) as source:
        initialize_batch_store(source)
        initialize_revision_store(source)
        initialize_issued_store(source)
        credential, = ingest(source, station_day, batch['batch'], readings).credentials
        r1 = freeze(source, station_day, 0, [QualityOperation(credential, 'approve', shifts['approve'])])
        with closing(sqlite3.connect(branch_path)) as branch:
            initialize_book_store(branch)
            initialize_statement_store(branch)
            accept(branch, station_day, encode_piece(issue(source, station_day, 0, r1)))
            totals.append(read(branch, station_day).total)
            entry = shifts['early_adjustment']
            adjust(branch, credential, entry['delta'], ReadingOrigin(*entry['origin']))
            totals.append(read(branch, station_day).total)
            first_id = seal(branch, station_day)
            r2 = freeze(source, station_day, r1, [QualityOperation(credential, 'hold', shifts['hold'])])
            accept(branch, station_day, encode_piece(issue(source, station_day, r1, r2)))
        release = shifts['release']
        r3 = freeze(source, station_day, r2, [QualityOperation(
            credential, 'release', release['decision'], release['quantity'], ReadingOrigin(*release['origin']),
        )])
        final_payload = encode_piece(issue(source, station_day, r2, r3))
    with closing(sqlite3.connect(branch_path)) as offline:
        entry = shifts['offline_adjustment']
        adjust(offline, credential, entry['delta'], ReadingOrigin(*entry['origin']))
        totals.append(read(offline, station_day).total)
        empty_id = seal(offline, station_day)
        accept(offline, station_day, final_payload)
        totals.append(read(offline, station_day).total)
        latest_id = seal(offline, station_day)
    ids = [first_id, empty_id, latest_id]
    with closing(sqlite3.connect(branch_path)) as next_shift:
        statements = [retrieve(next_shift, number) for number in ids]
    checks = [reconcile_statement(statement) for statement in statements]
    if any(not check.matches for check in checks):
        raise ValueError('封存账复算不符')
    return {'current': totals, 'sealed': [statement.total for statement in statements],
            'reconciled': [check.total for check in checks], 'statement_ids': ids,
            'revisions': [statement.revision for statement in statements],
            'first_line': _line(statements[0]), 'last_line': _line(statements[-1]),
            'empty_line_count': len(statements[1].lines)}


def main():
    with TemporaryDirectory(prefix='qingwan-demo-') as directory:
        result = run_workday(Path(directory))
    expected = [7, 9, 0, 14]
    downstream_expected = {'sealed': [9, 0, 14], 'reconciled': [9, 0, 14], 'empty_line_count': 0}
    downstream_actual = {key: result[key] for key in downstream_expected}
    if result['current'] != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'交班结果与手算不符: {result!r}')
    ids = result['statement_ids']
    print(f'在用合计 {result["current"]} L，成果号 {ids}，按原号复算 {result["reconciled"]} L')
    batch, shifts = _inputs()
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v8.by_original_id', 'input': {'batch': batch, 'shifts': shifts},
        'expected': expected, 'actual': result['current'],
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': ['intake/batches.py', 'quality/revisions.py', 'exchange/issue.py',
                       'ledger/accept.py', 'ledger/daily.py', 'archive/seal.py',
                       'archive/retrieve.py', 'archive/reconcile.py'],
        'oracle': '手算 7、7+2、空账 0、11+2+1。逐号领取的预期合计为 9、0、14。',
    }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
