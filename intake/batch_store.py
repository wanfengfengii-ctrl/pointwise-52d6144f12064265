import sqlite3
from contextlib import contextmanager

from intake.observations import (BatchReceipt, Observation, SampleCredential,
                                 SampleInput, StationDay, quantity_from_record,
                                 quantity_record)
from intake.origins import ReadingOrigin


def initialize_batch_store(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError('initialize the source outside a transaction')
    connection.execute('PRAGMA foreign_keys = ON')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS station_days (
            station TEXT NOT NULL, day TEXT NOT NULL,
            PRIMARY KEY (station, day)
        );
        CREATE TABLE IF NOT EXISTS batches (
            station TEXT NOT NULL, day TEXT NOT NULL, sequence INTEGER NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (station, day, sequence), UNIQUE (station, day, name),
            FOREIGN KEY (station, day) REFERENCES station_days(station, day)
        );
        CREATE TABLE IF NOT EXISTS observations (
            station TEXT NOT NULL, day TEXT NOT NULL, sequence INTEGER NOT NULL,
            position INTEGER NOT NULL, number TEXT NOT NULL, quantity TEXT NOT NULL,
            document TEXT NOT NULL, line INTEGER NOT NULL,
            PRIMARY KEY (station, day, sequence, position),
            FOREIGN KEY (station, day, sequence) REFERENCES batches(station, day, sequence)
        );
        CREATE INDEX IF NOT EXISTS observation_number
            ON observations(station, day, number, sequence DESC);
    ''')


@contextmanager
def batch_transaction(connection: sqlite3.Connection):
    connection.execute('PRAGMA foreign_keys = ON')
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def record_batch(connection: sqlite3.Connection, station_day: StationDay,
                 name: str, samples: tuple[SampleInput, ...]) -> BatchReceipt:
    connection.execute('INSERT OR IGNORE INTO station_days VALUES (?, ?)', station_day.key)
    sequence, = connection.execute('''
        SELECT coalesce(max(sequence), 0) + 1 FROM batches WHERE station=? AND day=?
    ''', station_day.key).fetchone()
    connection.execute('INSERT INTO batches VALUES (?, ?, ?, ?)',
                       (*station_day.key, sequence, name))
    credentials = []
    for position, sample in enumerate(samples, 1):
        connection.execute('INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
            *station_day.key, sequence, position, sample.number,
            quantity_record(sample.quantity), sample.origin.document, sample.origin.line,
        ))
        credentials.append(SampleCredential(station_day, sequence, position, sample.number))
    return BatchReceipt(station_day, name, sequence, tuple(credentials))


def _observation(station_day: StationDay, row: tuple) -> Observation:
    sequence, position, number, quantity, document, line = row
    credential = SampleCredential(station_day, sequence, position, number)
    return Observation(credential, quantity_from_record(quantity), ReadingOrigin(document, line))


def read_batch(connection: sqlite3.Connection, station_day: StationDay,
               name: str) -> tuple[Observation, ...] | None:
    rows = connection.execute('''
        SELECT o.sequence, o.position, o.number, o.quantity, o.document, o.line
        FROM batches AS b JOIN observations AS o USING (station, day, sequence)
        WHERE b.station=? AND b.day=? AND b.name=? ORDER BY o.position
    ''', (*station_day.key, name)).fetchall()
    return tuple(_observation(station_day, row) for row in rows) if rows else None


def read_latest_observation(connection: sqlite3.Connection, station_day: StationDay,
                            number: str) -> Observation | None:
    row = connection.execute('''
        SELECT sequence, position, number, quantity, document, line FROM observations
        WHERE station=? AND day=? AND number=? ORDER BY sequence DESC LIMIT 1
    ''', (*station_day.key, number)).fetchone()
    return _observation(station_day, row) if row is not None else None


def read_observation(connection: sqlite3.Connection,
                     credential: SampleCredential) -> Observation | None:
    row = connection.execute('''
        SELECT sequence, position, number, quantity, document, line FROM observations
        WHERE station=? AND day=? AND sequence=? AND position=? AND number=?
    ''', (*credential.station_day.key, *credential.order, credential.number)).fetchone()
    return _observation(credential.station_day, row) if row is not None else None
