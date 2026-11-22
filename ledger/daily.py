import sqlite3
from dataclasses import dataclass

from exchange.envelope import SupplyRow
from intake.observations import StationDay, validate_station_day
from ledger.adjustments import Adjustment, read_adjustments
from ledger.book_store import book_transaction, read_book_state
from ledger.eligibility import credential_for_row


@dataclass(frozen=True)
class DailyLine:
    row: SupplyRow
    adjustments: tuple[Adjustment, ...]
    total: int


@dataclass(frozen=True)
class DailyBook:
    station_day: StationDay
    revision: int
    lines: tuple[DailyLine, ...]
    total: int


def read_in_transaction(connection: sqlite3.Connection, station_day: StationDay) -> DailyBook | None:
    state = read_book_state(connection, station_day)
    if state is None:
        return None
    lines = []
    for row in state.rows:
        credential = credential_for_row(state.eligibility, row)
        adjustments = read_adjustments(connection, credential)
        total = row.quantity + sum(item.delta for item in adjustments)
        lines.append(DailyLine(row, adjustments, total))
    return DailyBook(station_day, state.revision, tuple(lines), sum(line.total for line in lines))


def read(connection: sqlite3.Connection, station_day: StationDay) -> DailyBook | None:
    validate_station_day(station_day)
    with book_transaction(connection, write=False):
        return read_in_transaction(connection, station_day)
