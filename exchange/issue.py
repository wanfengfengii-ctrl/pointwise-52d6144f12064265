import sqlite3

from exchange.envelope import SupplyPiece
from exchange.evidence import build_evidence
from exchange.issued_store import record_issue
from exchange.verification import verify_piece
from intake.batch_store import batch_transaction
from intake.observations import StationDay, validate_station_day
from intake.origins import valid_index
from quality.snapshots import read_snapshot


class IssueRejected(ValueError):
    pass


def issue(connection: sqlite3.Connection, station_day: StationDay,
          base: int, target: int) -> SupplyPiece:
    try:
        validate_station_day(station_day)
        if not valid_index(base, 0) or not valid_index(target) or target <= base:
            raise IssueRejected('target must follow the requested base revision')
        with batch_transaction(connection):
            start = read_snapshot(connection, station_day, base)
            finish = read_snapshot(connection, station_day, target)
            if start is None or finish is None:
                raise IssueRejected('both revisions must be frozen for this station day')
            rows, evidence = build_evidence(start, finish)
            issued = record_issue(connection, SupplyPiece(0, station_day, base, target, rows, evidence))
            verify_piece(issued, station_day)
    except IssueRejected:
        raise
    except (ValueError, sqlite3.Error) as error:
        raise IssueRejected(str(error)) from error
    return issued
