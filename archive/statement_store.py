import json
import sqlite3

from archive.statements import ClosedAdjustment, ClosedLine, ClosedStatement
from intake.observations import (StationDay, credential_from_record, credential_record,
                                 quantity_from_record, quantity_record)
from intake.origins import origin_from_record, origin_record
from ledger.vouchers import voucher_from_record, voucher_record


def initialize_statement_store(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS closed_statements (
            statement_id INTEGER PRIMARY KEY, station TEXT NOT NULL, day TEXT NOT NULL,
            revision INTEGER NOT NULL, lines TEXT NOT NULL, total TEXT NOT NULL,
            FOREIGN KEY (station, day) REFERENCES books(station, day)
        )
    ''')


def _encode_lines(lines: tuple[ClosedLine, ...]) -> str:
    return json.dumps([
        [credential_record(line.credential), quantity_record(line.quantity), origin_record(line.origin),
         [[item.adjustment_id, credential_record(item.credential), quantity_record(item.delta),
           origin_record(item.origin)] for item in line.adjustments], quantity_record(line.total),
         [voucher_record(item) for item in line.incorporated]]
        for line in lines
    ])


def _decode_lines(payload: str) -> tuple[ClosedLine, ...]:
    lines = []
    for record in json.loads(payload):
        if len(record) == 5:
            credential, quantity, origin, adjustments, total = record
            incorporated = ()
        elif len(record) == 6:
            credential, quantity, origin, adjustments, total, incorporated = record
        else:
            raise ValueError('invalid closed line record')
        entries = tuple(
            ClosedAdjustment(number, credential_from_record(owner), quantity_from_record(delta),
                             origin_from_record(source))
            for number, owner, delta, source in adjustments
        )
        lines.append(ClosedLine(credential_from_record(credential), quantity_from_record(quantity),
                                origin_from_record(origin), entries, quantity_from_record(total),
                                tuple(voucher_from_record(item) for item in incorporated)))
    return tuple(lines)


def insert_statement(connection: sqlite3.Connection, statement: ClosedStatement) -> int:
    result = connection.execute('''
        INSERT INTO closed_statements(station, day, revision, lines, total) VALUES (?, ?, ?, ?, ?)
    ''', (*statement.station_day.key, statement.revision, _encode_lines(statement.lines),
          quantity_record(statement.total)))
    return result.lastrowid


def read_statement(connection: sqlite3.Connection, statement_id: int) -> ClosedStatement | None:
    stored = connection.execute('''
        SELECT station, day, revision, lines, total FROM closed_statements WHERE statement_id=?
    ''', (statement_id,)).fetchone()
    if stored is None:
        return None
    station, day, revision, lines, total = stored
    return ClosedStatement(statement_id, StationDay(station, day), revision,
                           _decode_lines(lines), quantity_from_record(total))
