import sqlite3
from collections.abc import Iterable

from intake.batch_store import batch_transaction, read_observation
from intake.observations import StationDay, validate_credential, validate_station_day
from intake.origins import is_label, valid_index
from quality.disposition import InvalidQualityOperation as InvalidQualityOperation
from quality.disposition import QualityOperation, apply_disposition
from quality.snapshots import (QualitySnapshot, read_current_revision, read_snapshot,
                               record_snapshot)
from quality.withdrawal import apply_withdrawal


def freeze(connection: sqlite3.Connection, station_day: StationDay, base: int,
           operations: Iterable[QualityOperation]) -> int:
    try:
        validate_station_day(station_day)
        proposed = tuple(operations)
        if not valid_index(base, 0) or not proposed:
            raise InvalidQualityOperation('provide a base revision and named operations')
        with batch_transaction(connection):
            if read_current_revision(connection, station_day) != base:
                raise InvalidQualityOperation('base must be the current revision of this station day')
            snapshot = read_snapshot(connection, station_day, base)
            dispositions = {row.credential: row for row in snapshot.dispositions}
            for operation in proposed:
                if (not isinstance(operation, QualityOperation) or not is_label(operation.decision)
                        or not is_label(operation.action)):
                    raise InvalidQualityOperation('a named quality operation is required')
                credential = operation.credential
                validate_credential(credential)
                if credential.station_day != station_day:
                    raise InvalidQualityOperation('the original sample belongs to another station day')
                previous = dispositions.get(credential)
                observation = (previous.observation if previous is not None
                               else read_observation(connection, credential))
                if observation is None:
                    raise InvalidQualityOperation('unknown original sample')
                if operation.action == 'withdraw':
                    updated = apply_withdrawal(previous, operation)
                else:
                    updated = apply_disposition(observation, previous, operation)
                dispositions[credential] = updated
            revision = base + 1
            if not valid_index(revision):
                raise InvalidQualityOperation('revision range exhausted')
            ordered = tuple(sorted(dispositions.values(), key=lambda row: row.credential.order))
            record_snapshot(connection, QualitySnapshot(station_day, revision, ordered, proposed))
    except InvalidQualityOperation:
        raise
    except (TypeError, ValueError) as error:
        raise InvalidQualityOperation(str(error)) from error
    return revision
