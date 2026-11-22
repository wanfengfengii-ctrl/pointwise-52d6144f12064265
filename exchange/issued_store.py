import sqlite3
from dataclasses import replace

from exchange.envelope import SupplyPiece, decode_piece, encode_piece
from intake.origins import valid_index


def initialize_issued_store(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS issued_pieces (
            issue_id INTEGER PRIMARY KEY, station TEXT NOT NULL, day TEXT NOT NULL,
            base INTEGER NOT NULL, target INTEGER NOT NULL, payload BLOB NOT NULL,
            FOREIGN KEY (station, day) REFERENCES station_days(station, day)
        )
    ''')


def record_issue(connection: sqlite3.Connection, piece: SupplyPiece) -> SupplyPiece:
    issue_id, = connection.execute('SELECT coalesce(max(issue_id), 0) + 1 FROM issued_pieces').fetchone()
    issued = replace(piece, issue_id=issue_id)
    connection.execute('INSERT INTO issued_pieces VALUES (?, ?, ?, ?, ?, ?)', (
        issued.issue_id, *issued.station_day.key, issued.base_revision,
        issued.target_revision, encode_piece(issued),
    ))
    return issued


def read_issued_piece(connection: sqlite3.Connection, issue_id: int) -> SupplyPiece | None:
    if not valid_index(issue_id):
        return None
    stored = connection.execute('SELECT payload FROM issued_pieces WHERE issue_id=?',
                                (issue_id,)).fetchone()
    return None if stored is None else decode_piece(stored[0])
