from dataclasses import dataclass

from exchange.envelope import SupplyPiece, SupplyRow, inventory_digest
from exchange.verification import InvalidSupplyPiece
from intake.observations import SampleCredential


class AdjustmentRejected(ValueError):
    pass


@dataclass(frozen=True)
class Eligibility:
    credentials: frozenset[SampleCredential]


def resolve_eligibility(piece: SupplyPiece, base: Eligibility,
                        base_rows: tuple[SupplyRow, ...]) -> Eligibility:
    evidence = piece.evidence
    if inventory_digest(base.credentials, base_rows) != evidence.base_digest:
        raise InvalidSupplyPiece('base inventory does not match the accepted book')
    retired = frozenset(evidence.retired)
    added = frozenset(evidence.added)
    if not retired <= base.credentials or added & base.credentials:
        raise InvalidSupplyPiece('eligibility change conflicts with the base inventory')
    target = (base.credentials - retired) | added
    if (len({value.order for value in target}) != len(target)
            or len({value.number for value in target}) != len(target)):
        raise InvalidSupplyPiece('conflicting original samples in the target inventory')
    if any(row.credential not in target for row in piece.rows):
        raise InvalidSupplyPiece('billable row has no original-sample eligibility')
    if inventory_digest(target, piece.rows) != evidence.target_digest:
        raise InvalidSupplyPiece('target inventory does not match the evidence')
    return Eligibility(target)


def require_adjustable(eligibility: Eligibility, credential: SampleCredential) -> None:
    if credential not in eligibility.credentials:
        raise AdjustmentRejected('credential is not adjustable in the accepted revision')


def credential_for_row(eligibility: Eligibility, row: SupplyRow) -> SampleCredential:
    if row.credential not in eligibility.credentials:
        raise InvalidSupplyPiece('stored billable row has no original-sample eligibility')
    return row.credential
