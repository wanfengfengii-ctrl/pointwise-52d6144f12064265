import sqlite3

from archive.statement_store import read_statement
from archive.statements import ClosedStatement
from intake.origins import valid_index


class StatementNotFound(LookupError):
    pass


def retrieve(connection: sqlite3.Connection, statement_id: int) -> ClosedStatement:
    if valid_index(statement_id):
        statement = read_statement(connection, statement_id)
        if statement is not None:
            return statement
    raise StatementNotFound(f'unknown statement: {statement_id!r}')
