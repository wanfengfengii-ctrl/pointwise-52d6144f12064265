import re

from exchange.envelope import SupplyEvidence, SupplyPiece, SupplyRow
from intake.observations import (SampleCredential, StationDay, validate_credential,
                                 validate_station_day)
from intake.origins import valid_index, validate_origin


class InvalidSupplyPiece(ValueError):
    pass


def _unique_ordered(credentials: tuple[SampleCredential, ...], station_day: StationDay) -> None:
    last_order = (0, 0)
    numbers = set()
    for credential in credentials:
        validate_credential(credential)
        if credential.station_day != station_day or credential.order <= last_order:
            raise InvalidSupplyPiece('invalid station binding or sample order')
        if credential.number in numbers:
            raise InvalidSupplyPiece('duplicate sample number in a supply collection')
        numbers.add(credential.number)
        last_order = credential.order


def verify_piece(piece: SupplyPiece, expected_station_day: StationDay | None = None) -> None:
    if not isinstance(piece, SupplyPiece):
        raise InvalidSupplyPiece('a supply piece is required')
    try:
        validate_station_day(piece.station_day)
    except ValueError as error:
        raise InvalidSupplyPiece(str(error)) from error
    if expected_station_day is not None and piece.station_day != expected_station_day:
        raise InvalidSupplyPiece('supply belongs to another station day')
    if (not valid_index(piece.issue_id) or not valid_index(piece.base_revision, 0)
            or not valid_index(piece.target_revision) or piece.target_revision <= piece.base_revision):
        raise InvalidSupplyPiece('invalid issue identity or revision interval')
    evidence = piece.evidence
    if not isinstance(evidence, SupplyEvidence):
        raise InvalidSupplyPiece('missing eligibility evidence')
    for digest in (evidence.base_digest, evidence.target_digest):
        if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
            raise InvalidSupplyPiece('invalid inventory digest')
    if any(not isinstance(values, tuple) for values in (piece.rows, evidence.added, evidence.retired)):
        raise InvalidSupplyPiece('supply collections must be tuples')
    try:
        for row in piece.rows:
            if not isinstance(row, SupplyRow) or type(row.quantity) is not int:
                raise InvalidSupplyPiece('invalid billable reading')
            validate_origin(row.origin)
        _unique_ordered(tuple(row.credential for row in piece.rows), piece.station_day)
        _unique_ordered(evidence.added, piece.station_day)
        _unique_ordered(evidence.retired, piece.station_day)
    except ValueError as error:
        raise InvalidSupplyPiece(str(error)) from error
    retired_orders = {value.order for value in evidence.retired}
    if any(value.order in retired_orders for value in evidence.added):
        raise InvalidSupplyPiece('a retired position cannot be reissued as an addition')
    if any(row.credential.order in retired_orders for row in piece.rows):
        raise InvalidSupplyPiece('a retired original sample cannot be billable')
