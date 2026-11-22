import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from exchange.envelope import SupplyRow, row_from_record, row_record
from intake.observations import StationDay, credential_from_record, credential_record
from ledger.eligibility import Eligibility


@dataclass(frozen=True)
class BookState:
    station_day: StationDay
    revision: int
    issue_id: int
    rows: tuple[SupplyRow, ...]
    eligibility: Eligibility


def initialize_book_store(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError('initialize the ledger outside a transaction')
    connection.execute('PRAGMA foreign_keys = ON')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS books (
            station TEXT NOT NULL, day TEXT NOT NULL, revision INTEGER NOT NULL,
            issue_id INTEGER NOT NULL, rows TEXT NOT NULL, eligibility TEXT NOT NULL,
            PRIMARY KEY (station, day)
        );
        CREATE TABLE IF NOT EXISTS adjustments (
            adjustment_id INTEGER PRIMARY KEY, station TEXT NOT NULL, day TEXT NOT NULL,
            batch_sequence INTEGER NOT NULL, position INTEGER NOT NULL, number TEXT NOT NULL,
            delta TEXT NOT NULL, document TEXT NOT NULL, line INTEGER NOT NULL,
            FOREIGN KEY (station, day) REFERENCES books(station, day)
        );
        CREATE INDEX IF NOT EXISTS adjustment_by_original
            ON adjustments(station, day, batch_sequence, position, number, adjustment_id);
    ''')


@contextmanager
def book_transaction(connection: sqlite3.Connection, *, write: bool = True):
    connection.execute('PRAGMA foreign_keys = ON')
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def read_book_state(connection: sqlite3.Connection, station_day: StationDay) -> BookState | None:
    stored = connection.execute('''
        SELECT revision, issue_id, rows, eligibility FROM books WHERE station=? AND day=?
    ''', station_day.key).fetchone()
    if stored is None:
        return None
    revision, issue_id, rows, eligible = stored
    return BookState(station_day, revision, issue_id,
                     tuple(row_from_record(row) for row in json.loads(rows)),
                     Eligibility(frozenset(credential_from_record(row) for row in json.loads(eligible))))


def replace_book_state(connection: sqlite3.Connection, book: BookState) -> None:
    eligible = sorted(book.eligibility.credentials, key=lambda value: value.order)
    connection.execute('''
        INSERT INTO books VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(station, day) DO UPDATE SET
            revision=excluded.revision, issue_id=excluded.issue_id,
            rows=excluded.rows, eligibility=excluded.eligibility
    ''', (*book.station_day.key, book.revision, book.issue_id,
          json.dumps([row_record(row) for row in book.rows]),
          json.dumps([credential_record(value) for value in eligible])))
