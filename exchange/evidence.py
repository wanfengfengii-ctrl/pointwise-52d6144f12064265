from exchange.envelope import (SupplyEvidence, SupplyEvidenceV2, SupplyRow, inventory_digest,
                               inventory_digest_v2)
from intake.observations import SampleCredential
from ledger.vouchers import AdjustmentVoucher
from quality.snapshots import QualitySnapshot


def _supply_view(snapshot: QualitySnapshot) -> tuple[set[SampleCredential], tuple[SupplyRow, ...]]:
    live = {row.credential for row in snapshot.dispositions if row.status in ('billable', 'held')}
    visible = tuple(SupplyRow(row.credential, row.quantity, row.origin) for row in snapshot.billable)
    return live, visible


def _eligibility_change(base: QualitySnapshot, target: QualitySnapshot
                        ) -> tuple[tuple[SampleCredential, ...], tuple[SampleCredential, ...]]:
    base_live, _ = _supply_view(base)
    target_live, _ = _supply_view(target)
    added = tuple(sorted(target_live - base_live, key=lambda value: value.order))
    retired = tuple(sorted(base_live - target_live, key=lambda value: value.order))
    return added, retired


def build_evidence(base: QualitySnapshot, target: QualitySnapshot
                   ) -> tuple[tuple[SupplyRow, ...], SupplyEvidence]:
    base_live, base_rows = _supply_view(base)
    target_live, target_rows = _supply_view(target)
    added, retired = _eligibility_change(base, target)
    return target_rows, SupplyEvidence(
        inventory_digest(base_live, base_rows), inventory_digest(target_live, target_rows),
        added, retired,
    )


def build_evidence_v2(base: QualitySnapshot, target: QualitySnapshot,
                      base_basis: tuple[AdjustmentVoucher, ...],
                      target_basis: tuple[AdjustmentVoucher, ...]
                      ) -> tuple[tuple[SupplyRow, ...], SupplyEvidenceV2]:
    base_live, base_rows = _supply_view(base)
    target_live, target_rows = _supply_view(target)
    added, retired = _eligibility_change(base, target)
    return target_rows, SupplyEvidenceV2(
        inventory_digest_v2(base_live, base_rows, base_basis),
        inventory_digest_v2(target_live, target_rows, target_basis),
        added, retired, tuple(target_basis),
    )
