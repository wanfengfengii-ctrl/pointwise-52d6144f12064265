import sqlite3
from dataclasses import dataclass

from intake.observations import (SampleCredential, StationDay, quantity_from_record,
                                 quantity_record, validate_credential)
from intake.origins import ReadingOrigin, is_label, valid_index, validate_origin
from ledger.book_store import book_transaction, read_book_state, read_branch_id
from ledger.eligibility import AdjustmentRejected as AdjustmentRejected
from ledger.eligibility import require_adjustable
from ledger.vouchers import AdjustmentVoucher as AdjustmentVoucher


@dataclass(frozen=True)
class Adjustment:
    adjustment_id: int
    credential: SampleCredential
    delta: int
    origin: ReadingOrigin


def configure_branch(connection: sqlite3.Connection, branch_id: str) -> str:
    try:
        if not is_label(branch_id):
            raise AdjustmentRejected('a non-empty branch id is required')
        with book_transaction(connection):
            stored = connection.execute(
                'SELECT branch_id FROM branch_identity WHERE singleton=1').fetchone()
            if stored is None:
                connection.execute('INSERT INTO branch_identity VALUES (1, ?)', (branch_id,))
            elif stored[0] != branch_id:
                raise AdjustmentRejected('the branch id is already bound and cannot be rebound')
    except AdjustmentRejected:
        raise
    except (ValueError, sqlite3.Error) as error:
        raise AdjustmentRejected(str(error)) from error
    return branch_id


def export_voucher(connection: sqlite3.Connection, adjustment_id: int) -> AdjustmentVoucher:
    try:
        branch_id = read_branch_id(connection)
        if branch_id is None:
            raise AdjustmentRejected('configure the branch before exporting vouchers')
        if not valid_index(adjustment_id):
            raise AdjustmentRejected(f'unknown adjustment: {adjustment_id!r}')
        stored = connection.execute('''
            SELECT station, day, batch_sequence, position, number, delta, document, line
            FROM adjustments WHERE adjustment_id=?
        ''', (adjustment_id,)).fetchone()
        if stored is None:
            raise AdjustmentRejected(f'unknown adjustment: {adjustment_id!r}')
        station, day, sequence, position, number, delta, document, line = stored
        credential = SampleCredential(StationDay(station, day), sequence, position, number)
        voucher = AdjustmentVoucher(branch_id, adjustment_id, credential,
                                    quantity_from_record(delta), ReadingOrigin(document, line))
    except AdjustmentRejected:
        raise
    except (ValueError, sqlite3.Error) as error:
        raise AdjustmentRejected(str(error)) from error
    return voucher


def adjust(connection: sqlite3.Connection, credential: SampleCredential,
           delta: int, origin: ReadingOrigin) -> Adjustment:
    try:
        validate_credential(credential)
        stored_delta = quantity_record(delta)
        validate_origin(origin)
        with book_transaction(connection):
            book = read_book_state(connection, credential.station_day)
            if book is None:
                raise AdjustmentRejected('no accepted book for this credential')
            require_adjustable(book.eligibility, credential)
            result = connection.execute('''
                INSERT INTO adjustments(station, day, batch_sequence, position, number,
                                        delta, document, line) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (*credential.station_day.key, *credential.order, credential.number,
                  stored_delta, origin.document, origin.line))
            adjustment = Adjustment(result.lastrowid, credential, delta, origin)
    except AdjustmentRejected:
        raise
    except (ValueError, sqlite3.Error) as error:
        raise AdjustmentRejected(str(error)) from error
    return adjustment


def read_adjustments(connection: sqlite3.Connection,
                     credential: SampleCredential) -> tuple[Adjustment, ...]:
    validate_credential(credential)
    rows = connection.execute('''
        SELECT adjustment_id, delta, document, line FROM adjustments
        WHERE station=? AND day=? AND batch_sequence=? AND position=? AND number=?
        ORDER BY adjustment_id
    ''', (*credential.station_day.key, *credential.order, credential.number))
    return tuple(Adjustment(number, credential, quantity_from_record(delta), ReadingOrigin(doc, line))
                 for number, delta, doc, line in rows)
