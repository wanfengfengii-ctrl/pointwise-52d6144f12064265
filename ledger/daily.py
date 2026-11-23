import sqlite3
from dataclasses import dataclass

from exchange.envelope import SupplyRow
from intake.observations import StationDay, validate_station_day
from ledger.adjustments import Adjustment, read_adjustments
from ledger.book_store import book_transaction, read_book_state, read_branch_id
from ledger.eligibility import credential_for_row
from ledger.vouchers import AdjustmentVoucher


@dataclass(frozen=True)
class DailyLine:
    row: SupplyRow
    adjustments: tuple[Adjustment, ...]
    total: int
    incorporated: tuple[AdjustmentVoucher, ...] = ()


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
    branch_id = read_branch_id(connection)
    own = {voucher.adjustment_id for voucher in state.incorporated
           if voucher.branch_id == branch_id}
    lines = []
    for row in state.rows:
        credential = credential_for_row(state.eligibility, row)
        adjustments = tuple(item for item in read_adjustments(connection, credential)
                            if item.adjustment_id not in own)
        incorporated = tuple(voucher for voucher in state.incorporated
                             if voucher.credential == credential)
        total = row.quantity + sum(item.delta for item in adjustments)
        lines.append(DailyLine(row, adjustments, total, incorporated))
    return DailyBook(station_day, state.revision, tuple(lines), sum(line.total for line in lines))


def read(connection: sqlite3.Connection, station_day: StationDay) -> DailyBook | None:
    validate_station_day(station_day)
    with book_transaction(connection, write=False):
        return read_in_transaction(connection, station_day)
