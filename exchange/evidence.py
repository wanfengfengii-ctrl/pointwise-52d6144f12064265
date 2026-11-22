from exchange.envelope import SupplyEvidence, SupplyRow, inventory_digest
from intake.observations import SampleCredential
from quality.snapshots import QualitySnapshot


def _supply_view(snapshot: QualitySnapshot) -> tuple[set[SampleCredential], tuple[SupplyRow, ...]]:
    live = {row.credential for row in snapshot.dispositions if row.status in ('billable', 'held')}
    visible = tuple(SupplyRow(row.credential, row.quantity, row.origin) for row in snapshot.billable)
    return live, visible


def build_evidence(base: QualitySnapshot, target: QualitySnapshot
                   ) -> tuple[tuple[SupplyRow, ...], SupplyEvidence]:
    base_live, base_rows = _supply_view(base)
    target_live, target_rows = _supply_view(target)
    added = tuple(sorted(target_live - base_live, key=lambda value: value.order))
    retired = tuple(sorted(base_live - target_live, key=lambda value: value.order))
    return target_rows, SupplyEvidence(
        inventory_digest(base_live, base_rows), inventory_digest(target_live, target_rows),
        added, retired,
    )
