import json
import sqlite3
from contextlib import closing
from pathlib import Path

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


INCORPORATION_ORIGIN = ReadingOrigin("源账并入单", 1)
CORRECTION_ORIGIN = ReadingOrigin("末班复测更正单", 3)


def prepare(source, branch_a, branch_b):
    """Set up the original V1 batch, two local books, and one old statement."""
    directory = Path(__file__).parent
    batch = json.loads((directory / "batch.json").read_text(encoding="utf-8"))
    shifts = json.loads((directory / "shifts.json").read_text(encoding="utf-8"))
    day = StationDay(batch["station"], batch["day"])
    samples = [SampleInput(row["number"], row["quantity"], row["unit"], ReadingOrigin(*row["origin"]))
               for row in batch["samples"]]
    initialize_batch_store(source)
    initialize_revision_store(source)
    initialize_issued_store(source)
    credential, = ingest(source, day, batch["batch"], samples).credentials
    first = freeze(source, day, 0, [QualityOperation(credential, "approve", shifts["approve"])])
    payload = encode_piece(issue(source, day, 0, first))
    entry = shifts["early_adjustment"]
    for branch in (branch_a, branch_b):
        initialize_book_store(branch)
        initialize_statement_store(branch)
        accept(branch, day, payload)
    a_one = adjust(branch_a, credential, entry["delta"], ReadingOrigin(*entry["origin"]))
    b_one = adjust(branch_b, credential, entry["delta"], ReadingOrigin(*entry["origin"]))
    old_statement = seal(branch_a, day)
    adjust(branch_a, credential, 3, ReadingOrigin("甲仓续核单", 1))
    return day, credential, first, old_statement, a_one, b_one


def run_control():
    with (closing(sqlite3.connect(":memory:")) as branch_a,
          closing(sqlite3.connect(":memory:")) as branch_b):
        with closing(sqlite3.connect(":memory:")) as source:
            day, credential, first, old_id, a_one, b_one = prepare(source, branch_a, branch_b)
            middle = freeze(source, day, first, [QualityOperation(
                credential, "correct", "普通更正至 9 L", 9, INCORPORATION_ORIGIN,
            )])
            final = freeze(source, day, middle, [QualityOperation(
                credential, "correct", "末班复测", 11, CORRECTION_ORIGIN,
            )])
            payload = encode_piece(issue(source, day, first, final))
        for branch in (branch_a, branch_b):
            accept(branch, day, payload)
        book_a, book_b = read(branch_a, day), read(branch_b, day)
        old = retrieve(branch_a, old_id)
        check = reconcile_statement(old)
        result = {"equal_local_adjustments": a_one == b_one,
                  "revision": final, "source_readings": [book_a.lines[0].row.quantity,
                                                           book_b.lines[0].row.quantity],
                  "totals": [book_a.total, book_b.total], "old_statement": old.total}
        expected = {"equal_local_adjustments": True, "revision": 3, "source_readings": [11, 11],
                    "totals": [16, 13], "old_statement": 9}
        if result != expected or not check.matches or check.total != 9:
            raise AssertionError((result, expected, check))
        return result


if __name__ == "__main__":
    print(json.dumps(run_control(), ensure_ascii=False, sort_keys=True))
