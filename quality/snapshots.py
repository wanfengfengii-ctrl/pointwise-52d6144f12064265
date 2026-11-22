import json
import sqlite3
from dataclasses import dataclass

from intake.batch_store import read_observation
from intake.observations import (SampleCredential, StationDay, quantity_from_record,
                                 quantity_record)
from intake.origins import origin_from_record, origin_record, valid_index
from quality.disposition import Disposition, QualityOperation


@dataclass(frozen=True)
class QualitySnapshot:
    station_day: StationDay
    revision: int
    dispositions: tuple[Disposition, ...]
    operations: tuple[QualityOperation, ...]

    @property
    def billable(self) -> tuple[Disposition, ...]:
        return tuple(row for row in self.dispositions if row.status == 'billable')


def initialize_revision_store(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS quality_revisions (
            station TEXT NOT NULL, day TEXT NOT NULL, revision INTEGER NOT NULL,
            dispositions TEXT NOT NULL, operations TEXT NOT NULL,
            PRIMARY KEY (station, day, revision),
            FOREIGN KEY (station, day) REFERENCES station_days(station, day)
        )
    ''')


def read_current_revision(connection: sqlite3.Connection, station_day: StationDay) -> int | None:
    row = connection.execute('''
        SELECT coalesce((SELECT max(revision) FROM quality_revisions AS q
                         WHERE q.station=s.station AND q.day=s.day), 0)
        FROM station_days AS s WHERE s.station=? AND s.day=?
    ''', station_day.key).fetchone()
    return None if row is None else row[0]


def read_snapshot(connection: sqlite3.Connection, station_day: StationDay,
                  revision: int) -> QualitySnapshot | None:
    if not valid_index(revision, 0):
        return None
    if revision == 0:
        if read_current_revision(connection, station_day) is None:
            return None
        return QualitySnapshot(station_day, 0, (), ())
    stored = connection.execute('''
        SELECT dispositions, operations FROM quality_revisions
        WHERE station=? AND day=? AND revision=?
    ''', (*station_day.key, revision)).fetchone()
    if stored is None:
        return None
    dispositions = []
    for key, status, quantity, origin, decision in json.loads(stored[0]):
        credential = SampleCredential(station_day, *key)
        observation = read_observation(connection, credential)
        if observation is None:
            raise sqlite3.DatabaseError('frozen revision refers to a missing observation')
        dispositions.append(Disposition(observation, status, quantity_from_record(quantity),
                                        origin_from_record(origin), decision))
    operations = []
    for key, action, decision, quantity, origin in json.loads(stored[1]):
        operations.append(QualityOperation(
            SampleCredential(station_day, *key), action, decision,
            None if quantity is None else quantity_from_record(quantity),
            None if origin is None else origin_from_record(origin),
        ))
    return QualitySnapshot(station_day, revision, tuple(dispositions), tuple(operations))


def _key(credential: SampleCredential) -> list:
    return [*credential.order, credential.number]


def record_snapshot(connection: sqlite3.Connection, snapshot: QualitySnapshot) -> None:
    dispositions = [
        [_key(row.credential), row.status, quantity_record(row.quantity),
         origin_record(row.origin), row.decision]
        for row in snapshot.dispositions
    ]
    operations = [
        [_key(operation.credential), operation.action, operation.decision,
         None if operation.quantity is None else quantity_record(operation.quantity),
         None if operation.origin is None else origin_record(operation.origin)]
        for operation in snapshot.operations
    ]
    connection.execute('INSERT INTO quality_revisions VALUES (?, ?, ?, ?, ?)', (
        *snapshot.station_day.key, snapshot.revision, json.dumps(dispositions),
        json.dumps(operations),
    ))
