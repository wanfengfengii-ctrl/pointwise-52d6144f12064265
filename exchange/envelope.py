import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from intake.observations import (SampleCredential, StationDay, credential_from_record,
                                 credential_record, quantity_from_record, quantity_record)
from intake.origins import ReadingOrigin, origin_from_record, origin_record
from ledger.vouchers import AdjustmentVoucher, voucher_from_record, voucher_record


@dataclass(frozen=True)
class SupplyRow:
    credential: SampleCredential
    quantity: int
    origin: ReadingOrigin


@dataclass(frozen=True)
class SupplyEvidence:
    base_digest: str
    target_digest: str
    added: tuple[SampleCredential, ...]
    retired: tuple[SampleCredential, ...]


@dataclass(frozen=True)
class SupplyPiece:
    issue_id: int
    station_day: StationDay
    base_revision: int
    target_revision: int
    rows: tuple[SupplyRow, ...]
    evidence: SupplyEvidence


@dataclass(frozen=True)
class SupplyEvidenceV2:
    base_digest: str
    target_digest: str
    added: tuple[SampleCredential, ...]
    retired: tuple[SampleCredential, ...]
    incorporated: tuple[AdjustmentVoucher, ...]


@dataclass(frozen=True)
class SupplyPieceV2:
    issue_id: int
    station_day: StationDay
    base_revision: int
    target_revision: int
    rows: tuple[SupplyRow, ...]
    evidence: SupplyEvidenceV2


def row_record(row: SupplyRow) -> list:
    return [credential_record(row.credential), quantity_record(row.quantity), origin_record(row.origin)]


def row_from_record(record: list) -> SupplyRow:
    if not isinstance(record, list) or len(record) != 3:
        raise ValueError('invalid supply row')
    credential, quantity, origin = record
    return SupplyRow(credential_from_record(credential), quantity_from_record(quantity),
                     origin_from_record(origin))


def _ordered_records(credentials: Iterable[SampleCredential]) -> list:
    return [credential_record(value) for value in sorted(credentials, key=lambda value: value.order)]


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')


def inventory_digest(credentials: Iterable[SampleCredential], rows: Iterable[SupplyRow]) -> str:
    return hashlib.sha256(_json_bytes([
        _ordered_records(credentials), [row_record(row) for row in rows],
    ])).hexdigest()


def inventory_digest_v2(credentials: Iterable[SampleCredential], rows: Iterable[SupplyRow],
                        vouchers: Iterable[AdjustmentVoucher]) -> str:
    return hashlib.sha256(_json_bytes([
        _ordered_records(credentials), [row_record(row) for row in rows],
        [voucher_record(voucher) for voucher in vouchers],
    ])).hexdigest()


def encode_piece(piece: SupplyPiece | SupplyPieceV2) -> bytes:
    proof = piece.evidence
    if isinstance(piece, SupplyPieceV2):
        return _json_bytes({
            'version': 2, 'issue_id': piece.issue_id, 'station_day': list(piece.station_day.key),
            'base': piece.base_revision, 'target': piece.target_revision,
            'rows': [row_record(row) for row in piece.rows],
            'evidence': {'base': proof.base_digest, 'target': proof.target_digest,
                         'added': [credential_record(value) for value in proof.added],
                         'retired': [credential_record(value) for value in proof.retired],
                         'incorporated': [voucher_record(value) for value in proof.incorporated]},
        })
    return _json_bytes({
        'version': 1, 'issue_id': piece.issue_id, 'station_day': list(piece.station_day.key),
        'base': piece.base_revision, 'target': piece.target_revision,
        'rows': [row_record(row) for row in piece.rows],
        'evidence': {'base': proof.base_digest, 'target': proof.target_digest,
                     'added': [credential_record(value) for value in proof.added],
                     'retired': [credential_record(value) for value in proof.retired]},
    })


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'duplicate field: {key}')
        value[key] = item
    return value


def _fields(value: object, names: set[str]) -> dict:
    if not isinstance(value, dict) or value.keys() != names:
        raise ValueError('unexpected supply fields')
    return value


def _array(value: object) -> list:
    if not isinstance(value, list):
        raise ValueError('expected a supply array')
    return value


def _credentials(value: object) -> tuple[SampleCredential, ...]:
    return tuple(credential_from_record(row) for row in _array(value))


def decode_piece(payload: bytes | str) -> SupplyPiece | SupplyPieceV2:
    if not isinstance(payload, (bytes, str)):
        raise ValueError('supply payload must be bytes or text')
    data = _fields(json.loads(payload, object_pairs_hook=_unique_object),
                   {'version', 'issue_id', 'station_day', 'base', 'target', 'rows', 'evidence'})
    if type(data['version']) is not int or data['version'] not in (1, 2):
        raise ValueError('unknown supply version')
    station_day = _array(data['station_day'])
    if len(station_day) != 2:
        raise ValueError('invalid station day in supply')
    day = StationDay(*station_day)
    rows = tuple(row_from_record(row) for row in _array(data['rows']))
    if data['version'] == 1:
        proof = _fields(data['evidence'], {'base', 'target', 'added', 'retired'})
        return SupplyPiece(
            data['issue_id'], day, data['base'], data['target'], rows,
            SupplyEvidence(proof['base'], proof['target'],
                           _credentials(proof['added']), _credentials(proof['retired'])),
        )
    proof = _fields(data['evidence'], {'base', 'target', 'added', 'retired', 'incorporated'})
    return SupplyPieceV2(
        data['issue_id'], day, data['base'], data['target'], rows,
        SupplyEvidenceV2(proof['base'], proof['target'],
                         _credentials(proof['added']), _credentials(proof['retired']),
                         tuple(voucher_from_record(row) for row in _array(proof['incorporated']))),
    )
