import sqlite3

from archive.statement_store import insert_statement
from archive.statements import ClosedAdjustment, ClosedLine, ClosedStatement
from intake.observations import StationDay, validate_station_day
from ledger.book_store import book_transaction
from ledger.daily import DailyLine, read_in_transaction


class SealRejected(ValueError):
    pass


def _close_line(line: DailyLine) -> ClosedLine:
    entries = tuple(ClosedAdjustment(item.adjustment_id, item.credential, item.delta, item.origin)
                    for item in line.adjustments)
    return ClosedLine(line.row.credential, line.row.quantity, line.row.origin, entries, line.total)


def seal(connection: sqlite3.Connection, station_day: StationDay) -> int:
    try:
        validate_station_day(station_day)
        with book_transaction(connection):
            book = read_in_transaction(connection, station_day)
            if book is None:
                raise SealRejected('no accepted book to seal')
            statement = ClosedStatement(0, station_day, book.revision,
                                        tuple(_close_line(line) for line in book.lines), book.total)
            number = insert_statement(connection, statement)
    except SealRejected:
        raise
    except (ValueError, sqlite3.Error) as error:
        raise SealRejected(str(error)) from error
    return number
