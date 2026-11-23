from dataclasses import dataclass

from exchange.envelope import (SupplyPiece, SupplyPieceV2, SupplyRow, inventory_digest,
                               inventory_digest_v2)
from exchange.verification import InvalidSupplyPiece
from intake.observations import SampleCredential
from ledger.vouchers import AdjustmentVoucher


class AdjustmentRejected(ValueError):
    pass


@dataclass(frozen=True)
class Eligibility:
    credentials: frozenset[SampleCredential]


def _eligibility_change(evidence, base: Eligibility) -> frozenset[SampleCredential]:
    retired = frozenset(evidence.retired)
    added = frozenset(evidence.added)
    if not retired <= base.credentials or added & base.credentials:
        raise InvalidSupplyPiece('eligibility change conflicts with the base inventory')
    target = (base.credentials - retired) | added
    if (len({value.order for value in target}) != len(target)
            or len({value.number for value in target}) != len(target)):
        raise InvalidSupplyPiece('conflicting original samples in the target inventory')
    return target


def _check_target(piece, evidence, target: frozenset[SampleCredential]) -> None:
    if any(row.credential not in target for row in piece.rows):
        raise InvalidSupplyPiece('billable row has no original-sample eligibility')


def resolve_eligibility(piece: SupplyPiece, base: Eligibility,
                        base_rows: tuple[SupplyRow, ...]) -> Eligibility:
    evidence = piece.evidence
    if inventory_digest(base.credentials, base_rows) != evidence.base_digest:
        raise InvalidSupplyPiece('base inventory does not match the accepted book')
    target = _eligibility_change(evidence, base)
    _check_target(piece, evidence, target)
    if inventory_digest(target, piece.rows) != evidence.target_digest:
        raise InvalidSupplyPiece('target inventory does not match the evidence')
    return Eligibility(target)


def resolve_eligibility_v2(piece: SupplyPieceV2, base: Eligibility,
                           base_rows: tuple[SupplyRow, ...],
                           base_basis: tuple[AdjustmentVoucher, ...]) -> Eligibility:
    evidence = piece.evidence
    if inventory_digest_v2(base.credentials, base_rows, base_basis) != evidence.base_digest:
        raise InvalidSupplyPiece('base inventory does not match the accepted book')
    target = _eligibility_change(evidence, base)
    _check_target(piece, evidence, target)
    if inventory_digest_v2(target, piece.rows, evidence.incorporated) != evidence.target_digest:
        raise InvalidSupplyPiece('target inventory does not match the evidence')
    return Eligibility(target)


def require_adjustable(eligibility: Eligibility, credential: SampleCredential) -> None:
    if credential not in eligibility.credentials:
        raise AdjustmentRejected('credential is not adjustable in the accepted revision')


def credential_for_row(eligibility: Eligibility, row: SupplyRow) -> SampleCredential:
    if row.credential not in eligibility.credentials:
        raise InvalidSupplyPiece('stored billable row has no original-sample eligibility')
    return row.credential
