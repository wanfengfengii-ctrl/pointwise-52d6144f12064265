import sqlite3
from collections.abc import Iterable

from intake.batch_store import batch_transaction, read_observation
from intake.observations import SampleCredential, StationDay, validate_credential, validate_station_day
from intake.origins import ReadingOrigin, is_label, valid_index, validate_origin
from ledger.vouchers import AdjustmentVoucher, validate_voucher
from quality.disposition import Disposition, InvalidQualityOperation as InvalidQualityOperation
from quality.disposition import QualityOperation, apply_disposition
from quality.snapshots import (QualitySnapshot, read_current_revision, read_incorporation,
                               read_snapshot, record_incorporation, record_snapshot)
from quality.withdrawal import apply_withdrawal

INCORPORATION_DECISION = '源账并入'


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


def incorporate(connection: sqlite3.Connection, station_day: StationDay, base: int,
                credential: SampleCredential, voucher: AdjustmentVoucher,
                origin: ReadingOrigin) -> int:
    try:
        validate_station_day(station_day)
        validate_credential(credential)
        validate_origin(origin)
        validate_voucher(voucher)
        if voucher.credential != credential:
            raise InvalidQualityOperation('the voucher names a different original sample')
        if credential.station_day != station_day:
            raise InvalidQualityOperation('the original sample belongs to another station day')
        if not valid_index(base, 0):
            raise InvalidQualityOperation('provide a base revision')
        with batch_transaction(connection):
            if read_current_revision(connection, station_day) != base:
                raise InvalidQualityOperation('base must be the current revision of this station day')
            snapshot = read_snapshot(connection, station_day, base)
            dispositions = {row.credential: row for row in snapshot.dispositions}
            previous = dispositions.get(credential)
            if previous is None or previous.status != 'billable':
                raise InvalidQualityOperation(
                    'the original sample is not billable at the current revision')
            if read_incorporation(connection, station_day, voucher.branch_id,
                                  voucher.adjustment_id) is not None:
                raise InvalidQualityOperation('the voucher is already incorporated')
            revision = base + 1
            if not valid_index(revision):
                raise InvalidQualityOperation('revision range exhausted')
            updated = Disposition(previous.observation, 'billable',
                                  previous.quantity + voucher.delta, origin,
                                  INCORPORATION_DECISION)
            dispositions[credential] = updated
            ordered = tuple(sorted(dispositions.values(), key=lambda row: row.credential.order))
            operation = QualityOperation(credential, 'incorporate', INCORPORATION_DECISION,
                                         updated.quantity, origin)
            record_snapshot(connection, QualitySnapshot(station_day, revision, ordered,
                                                        (operation,)))
            record_incorporation(connection, station_day, revision, voucher)
    except InvalidQualityOperation:
        raise
    except (TypeError, ValueError) as error:
        raise InvalidQualityOperation(str(error)) from error
    return revision
