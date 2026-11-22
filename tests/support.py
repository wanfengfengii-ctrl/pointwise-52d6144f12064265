import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from archive.statement_store import initialize_statement_store
from exchange.envelope import SupplyPiece, encode_piece
from exchange.issue import issue
from exchange.issued_store import initialize_issued_store
from intake.batch_store import initialize_batch_store
from intake.batches import ingest
from intake.observations import SampleCredential, SampleInput, StationDay
from intake.origins import ReadingOrigin
from ledger.accept import accept
from ledger.adjustments import adjust
from ledger.book_store import initialize_book_store
from quality.disposition import QualityOperation
from quality.revisions import freeze
from quality.snapshots import initialize_revision_store

DAY = StationDay('清湾北站', '2025-04-16')
OTHER_DAY = StationDay('清湾南站', '2025-04-16')
ORIGINAL = ReadingOrigin('站务原单', 1)
RESTORED = ReadingOrigin('复测原单', 3)
PLUS_TWO = ReadingOrigin('早班核账单', 2)
PLUS_ONE = ReadingOrigin('白班核账单', 4)


def sample(number='K', quantity=7, unit='L', origin=ORIGINAL):
    return SampleInput(number, quantity, unit, origin)


def operation(credential, action, quantity=None, origin=None):
    return QualityOperation(credential, action, f'清湾质控记录/{action}', quantity, origin)


def initialize_source(connection):
    initialize_batch_store(connection)
    initialize_revision_store(connection)
    initialize_issued_store(connection)


def initialize_branch(connection):
    initialize_book_store(connection)
    initialize_statement_store(connection)


def prepare_first(connection, station_day=DAY, reading=None):
    reading = sample() if reading is None else reading
    credential, = ingest(connection, station_day, '早班实收', [reading]).credentials
    freeze(connection, station_day, 0, [operation(credential, 'approve')])
    return credential, issue(connection, station_day, 0, 1)


def take(connection, piece):
    return accept(connection, piece.station_day, encode_piece(piece))


def first_book(connection, piece, credential):
    take(connection, piece)
    return adjust(connection, credential, 2, PLUS_TWO)


@dataclass(frozen=True)
class ShiftPieces:
    original: SampleCredential
    current: SampleCredential
    initial: SupplyPiece
    middle: SupplyPiece
    final: SupplyPiece
    skip: SupplyPiece


def build_shifts(connection, replacement):
    original, initial = prepare_first(connection)
    if replacement:
        freeze(connection, DAY, 1, [operation(original, 'withdraw')])
        current, = ingest(connection, DAY, '补采批次', [sample(quantity=11, origin=RESTORED)]).credentials
        freeze(connection, DAY, 2, [operation(current, 'approve'), operation(current, 'hold')])
        freeze(connection, DAY, 3, [operation(current, 'release')])
    else:
        current = original
        freeze(connection, DAY, 1, [operation(original, 'hold')])
        freeze(connection, DAY, 2, [operation(original, 'correct', 8, RESTORED)])
        freeze(connection, DAY, 3, [operation(original, 'release', 11, RESTORED)])
    return ShiftPieces(original, current, initial, issue(connection, DAY, 1, 3),
                       issue(connection, DAY, 3, 4), issue(connection, DAY, 1, 4))


def arm_commit_failure(connection, target):
    events = {'accept': 'UPDATE OF revision ON books', 'first_accept': 'INSERT ON books',
              'seal': 'INSERT ON closed_statements'}
    event = events[target]
    connection.executescript(f'''
        PRAGMA foreign_keys = ON;
        CREATE TABLE test_commit_parent (id INTEGER PRIMARY KEY);
        CREATE TABLE test_commit_child (
            id INTEGER REFERENCES test_commit_parent(id) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TRIGGER test_commit_failure AFTER {event}
        BEGIN
            INSERT INTO test_commit_child VALUES (47);
        END;
    ''')


def allow_commit(connection):
    with connection:
        connection.execute('INSERT INTO test_commit_parent VALUES (47)')


class WorkdayCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='qingwan-test-')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def connect(self, name):
        connection = sqlite3.connect(self.directory / f'{name}.sqlite3')
        self.addCleanup(connection.close)
        return connection

    def source(self, name='source'):
        connection = self.connect(name)
        initialize_source(connection)
        return connection

    def branch(self, name='branch'):
        connection = self.connect(name)
        initialize_branch(connection)
        return connection
