from dataclasses import dataclass

from intake.observations import (SampleCredential, credential_from_record, credential_record,
                                 quantity_from_record, quantity_record, validate_credential)
from intake.origins import (ReadingOrigin, is_label, origin_from_record, origin_record,
                            valid_index, validate_origin)


@dataclass(frozen=True)
class AdjustmentVoucher:
    branch_id: str
    adjustment_id: int
    credential: SampleCredential
    delta: int
    origin: ReadingOrigin


def validate_voucher(voucher: AdjustmentVoucher) -> None:
    if not isinstance(voucher, AdjustmentVoucher) or not is_label(voucher.branch_id):
        raise ValueError('an adjustment voucher with a named branch is required')
    if not valid_index(voucher.adjustment_id):
        raise ValueError('invalid original adjustment id in voucher')
    validate_credential(voucher.credential)
    if type(voucher.delta) is not int:
        raise ValueError('voucher delta must be an integer')
    validate_origin(voucher.origin)


def voucher_record(voucher: AdjustmentVoucher) -> list:
    validate_voucher(voucher)
    return [voucher.branch_id, voucher.adjustment_id, credential_record(voucher.credential),
            quantity_record(voucher.delta), origin_record(voucher.origin)]


def voucher_from_record(record: list) -> AdjustmentVoucher:
    if not isinstance(record, list) or len(record) != 5:
        raise ValueError('invalid voucher record')
    branch_id, adjustment_id, credential, delta, origin = record
    voucher = AdjustmentVoucher(branch_id, adjustment_id, credential_from_record(credential),
                                quantity_from_record(delta), origin_from_record(origin))
    validate_voucher(voucher)
    return voucher
