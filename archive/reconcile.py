from dataclasses import dataclass

from archive.statements import ClosedStatement
from intake.observations import validate_credential, validate_station_day
from intake.origins import valid_index, validate_origin
from ledger.vouchers import AdjustmentVoucher, validate_voucher


@dataclass(frozen=True)
class Reconciliation:
    line_totals: tuple[int, ...]
    total: int
    issues: tuple[str, ...]

    @property
    def matches(self) -> bool:
        return not self.issues


def reconcile_statement(statement: ClosedStatement) -> Reconciliation:
    validate_station_day(statement.station_day)
    totals = []
    issues = []
    last_order = (0, 0)
    numbers = set()
    adjustment_ids = set()
    vouchers = set()
    for line in statement.lines:
        credential = line.credential
        validate_credential(credential)
        validate_origin(line.origin)
        if credential.station_day != statement.station_day:
            issues.append(f'foreign station day at {credential.order}')
        if credential.order <= last_order or credential.number in numbers:
            issues.append(f'invalid original order at {credential.order}')
        last_order = credential.order
        numbers.add(credential.number)
        if type(line.quantity) is not int:
            raise ValueError('sealed reading must be an integer')
        total = line.quantity
        prior_id = 0
        for item in line.adjustments:
            validate_origin(item.origin)
            if not valid_index(item.adjustment_id) or type(item.delta) is not int:
                raise ValueError('invalid sealed adjustment')
            if item.credential != credential:
                issues.append(f'adjustment {item.adjustment_id} belongs to another original sample')
            if item.adjustment_id <= prior_id or item.adjustment_id in adjustment_ids:
                issues.append(f'invalid adjustment order or duplicate {item.adjustment_id}')
            adjustment_ids.add(item.adjustment_id)
            prior_id = item.adjustment_id
            total += item.delta
        for voucher in line.incorporated:
            if not isinstance(voucher, AdjustmentVoucher):
                raise ValueError('invalid sealed voucher')
            try:
                validate_voucher(voucher)
            except ValueError as error:
                raise ValueError(f'invalid sealed voucher: {error}') from error
            if voucher.credential != credential:
                issues.append(f'incorporated voucher {voucher.adjustment_id} '
                              'belongs to another original sample')
            key = (voucher.branch_id, voucher.adjustment_id)
            if key in vouchers:
                issues.append(f'duplicate incorporated voucher {voucher.adjustment_id}')
            vouchers.add(key)
        if type(line.total) is not int or total != line.total:
            issues.append(f'line total differs at {credential.order}')
        totals.append(total)
    total = sum(totals)
    if type(statement.total) is not int or total != statement.total:
        issues.append('statement total differs')
    return Reconciliation(tuple(totals), total, tuple(issues))
