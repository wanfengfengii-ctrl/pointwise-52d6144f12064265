import re

from exchange.envelope import (SupplyEvidence, SupplyEvidenceV2, SupplyPiece, SupplyPieceV2,
                               SupplyRow)
from intake.observations import (SampleCredential, StationDay, validate_credential,
                                 validate_station_day)
from intake.origins import valid_index, validate_origin
from ledger.vouchers import validate_voucher


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


def _verify_common(piece, evidence, expected_station_day: StationDay | None) -> None:
    try:
        validate_station_day(piece.station_day)
    except ValueError as error:
        raise InvalidSupplyPiece(str(error)) from error
    if expected_station_day is not None and piece.station_day != expected_station_day:
        raise InvalidSupplyPiece('supply belongs to another station day')
    if (not valid_index(piece.issue_id) or not valid_index(piece.base_revision, 0)
            or not valid_index(piece.target_revision) or piece.target_revision <= piece.base_revision):
        raise InvalidSupplyPiece('invalid issue identity or revision interval')
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


def _verify_incorporated(piece: SupplyPieceV2) -> None:
    vouchers = piece.evidence.incorporated
    if not isinstance(vouchers, tuple):
        raise InvalidSupplyPiece('supply collections must be tuples')
    seen = set()
    for voucher in vouchers:
        try:
            validate_voucher(voucher)
        except ValueError as error:
            raise InvalidSupplyPiece(str(error)) from error
        if voucher.credential.station_day != piece.station_day:
            raise InvalidSupplyPiece('an incorporated voucher belongs to another station day')
        key = (voucher.branch_id, voucher.adjustment_id)
        if key in seen:
            raise InvalidSupplyPiece('duplicate incorporated voucher')
        seen.add(key)


def verify_piece(piece: SupplyPiece | SupplyPieceV2,
                 expected_station_day: StationDay | None = None) -> None:
    if isinstance(piece, SupplyPieceV2):
        if not isinstance(piece.evidence, SupplyEvidenceV2):
            raise InvalidSupplyPiece('missing eligibility evidence')
        _verify_common(piece, piece.evidence, expected_station_day)
        _verify_incorporated(piece)
        return
    if not isinstance(piece, SupplyPiece):
        raise InvalidSupplyPiece('a supply piece is required')
    if not isinstance(piece.evidence, SupplyEvidence):
        raise InvalidSupplyPiece('missing eligibility evidence')
    _verify_common(piece, piece.evidence, expected_station_day)
