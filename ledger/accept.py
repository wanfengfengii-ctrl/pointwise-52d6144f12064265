import sqlite3

from exchange.envelope import SupplyPiece, decode_piece
from exchange.verification import verify_piece
from intake.observations import StationDay, validate_station_day
from ledger.book_store import BookState, book_transaction, read_book_state, replace_book_state
from ledger.eligibility import Eligibility, resolve_eligibility


class AcceptanceError(ValueError):
    pass


def accept(connection: sqlite3.Connection, station_day: StationDay,
           supplied: SupplyPiece | bytes | str) -> int:
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
            eligibility = resolve_eligibility(piece, previous, base_rows)
            replace_book_state(connection, BookState(
                station_day, piece.target_revision, piece.issue_id, piece.rows, eligibility,
            ))
    except AcceptanceError:
        raise
    except (ValueError, RecursionError, sqlite3.Error) as error:
        raise AcceptanceError(str(error)) from error
    return piece.target_revision
