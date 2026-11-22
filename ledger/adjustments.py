import sqlite3
from dataclasses import dataclass

from intake.observations import (SampleCredential, quantity_from_record, quantity_record,
                                 validate_credential)
from intake.origins import ReadingOrigin, validate_origin
from ledger.book_store import book_transaction, read_book_state
from ledger.eligibility import AdjustmentRejected as AdjustmentRejected
from ledger.eligibility import require_adjustable


@dataclass(frozen=True)
class Adjustment:
    adjustment_id: int
    credential: SampleCredential
    delta: int
    origin: ReadingOrigin


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
