import sqlite3

from exchange.envelope import SupplyPiece, SupplyPieceV2, decode_piece
from exchange.verification import verify_piece
from intake.observations import StationDay, validate_station_day
from ledger.adjustments import export_voucher
from ledger.book_store import (BookState, book_transaction, read_book_state, read_branch_id,
                               replace_book_state)
from ledger.eligibility import Eligibility, resolve_eligibility, resolve_eligibility_v2
from ledger.vouchers import AdjustmentVoucher


class AcceptanceError(ValueError):
    pass


def _check_own_vouchers(connection: sqlite3.Connection, branch_id: str,
                        vouchers: tuple[AdjustmentVoucher, ...]) -> None:
    for voucher in vouchers:
        if voucher.branch_id != branch_id:
            continue
        if export_voucher(connection, voucher.adjustment_id) != voucher:
            raise AcceptanceError(
                f'voucher {voucher.adjustment_id} does not match the local adjustment record')


def accept(connection: sqlite3.Connection, station_day: StationDay,
           supplied: SupplyPiece | SupplyPieceV2 | bytes | str) -> int:
    try:
        validate_station_day(station_day)
        piece = decode_piece(supplied) if isinstance(supplied, (bytes, str)) else supplied
        verify_piece(piece, station_day)
        with book_transaction(connection):
            current = read_book_state(connection, station_day)
            base = 0 if current is None else current.revision
            if piece.base_revision != base:
                raise AcceptanceError(f'base revision mismatch: expected {base}, received {piece.base_revision}')
            previous = Eligibility(frozenset()) if current is None else current.eligibility
            base_rows = () if current is None else current.rows
            base_basis = () if current is None else current.incorporated
            if isinstance(piece, SupplyPieceV2):
                branch_id = read_branch_id(connection)
                if branch_id is None:
                    raise AcceptanceError('a configured branch is required to accept V2 supply')
                eligibility = resolve_eligibility_v2(piece, previous, base_rows, base_basis)
                _check_own_vouchers(connection, branch_id, piece.evidence.incorporated)
                incorporated = piece.evidence.incorporated
            else:
                if base_basis:
                    raise AcceptanceError('V1 supply cannot extend an incorporated book')
                eligibility = resolve_eligibility(piece, previous, base_rows)
                incorporated = ()
            replace_book_state(connection, BookState(
                station_day, piece.target_revision, piece.issue_id, piece.rows, eligibility,
                incorporated,
            ))
    except AcceptanceError:
        raise
    except (ValueError, RecursionError, sqlite3.Error) as error:
        raise AcceptanceError(str(error)) from error
    return piece.target_revision
