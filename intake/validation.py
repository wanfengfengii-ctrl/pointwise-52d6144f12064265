from collections.abc import Iterable

from intake.observations import SampleInput, StationDay, validate_station_day
from intake.origins import is_label, validate_origin

SAMPLE_UNIT = 'L'


class BatchValidationError(ValueError):
    pass


def validate_batch(station_day: StationDay, name: str,
                   samples: Iterable[SampleInput]) -> tuple[SampleInput, ...]:
    try:
        validate_station_day(station_day)
        proposed = tuple(samples)
    except (TypeError, ValueError) as error:
        raise BatchValidationError(str(error)) from error
    if not is_label(name) or not proposed:
        raise BatchValidationError('provide a batch name and at least one sample')
    numbers = set()
    for position, sample in enumerate(proposed, 1):
        if not isinstance(sample, SampleInput) or not is_label(sample.number):
            raise BatchValidationError(f'row {position} has no valid sample number')
        if sample.unit != SAMPLE_UNIT or type(sample.quantity) is not int:
            raise BatchValidationError(f'row {position} requires an integer reading in L')
        try:
            validate_origin(sample.origin)
        except ValueError as error:
            raise BatchValidationError(f'row {position}: {error}') from error
        if sample.number in numbers:
            raise BatchValidationError(f'duplicate sample number: {sample.number}')
        numbers.add(sample.number)
    return proposed
