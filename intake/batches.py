import sqlite3
from collections.abc import Iterable

from intake.batch_store import batch_transaction, record_batch
from intake.observations import BatchReceipt, SampleInput, StationDay
from intake.validation import BatchValidationError, validate_batch
from quality.withdrawal import require_reusable_number


def ingest(connection: sqlite3.Connection, station_day: StationDay, name: str,
           samples: Iterable[SampleInput]) -> BatchReceipt:
    proposed = validate_batch(station_day, name, samples)
    try:
        with batch_transaction(connection):
            for sample in proposed:
                require_reusable_number(connection, station_day, sample.number)
            receipt = record_batch(connection, station_day, name, proposed)
    except (ValueError, sqlite3.IntegrityError) as error:
        raise BatchValidationError(str(error)) from error
    return receipt
