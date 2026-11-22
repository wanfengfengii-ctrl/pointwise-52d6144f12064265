import sqlite3
from dataclasses import replace

from intake.batch_store import read_latest_observation
from intake.observations import StationDay
from quality.disposition import Disposition, InvalidQualityOperation, QualityOperation
from quality.snapshots import read_current_revision, read_snapshot


def apply_withdrawal(previous: Disposition | None, operation: QualityOperation) -> Disposition:
    if previous is None or previous.status not in ('held', 'billable'):
        raise InvalidQualityOperation('withdrawal requires a live, assessed original sample')
    if operation.quantity is not None or operation.origin is not None:
        raise InvalidQualityOperation('withdrawal records a decision without a reading correction')
    return replace(previous, status='withdrawn', decision=operation.decision)


def require_reusable_number(connection: sqlite3.Connection, station_day: StationDay,
                            number: str) -> None:
    existing = read_latest_observation(connection, station_day, number)
    if existing is None:
        return
    revision = read_current_revision(connection, station_day)
    snapshot = read_snapshot(connection, station_day, revision)
    if snapshot is not None:
        for disposition in snapshot.dispositions:
            if disposition.credential == existing.credential and disposition.status == 'withdrawn':
                return
    raise ValueError(f'sample number still reserved: {number}')
