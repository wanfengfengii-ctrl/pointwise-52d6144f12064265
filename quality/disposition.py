from dataclasses import dataclass

from intake.observations import Observation, SampleCredential
from intake.origins import ReadingOrigin, validate_origin


class InvalidQualityOperation(ValueError):
    pass


@dataclass(frozen=True)
class QualityOperation:
    credential: SampleCredential
    action: str
    decision: str
    quantity: int | None = None
    origin: ReadingOrigin | None = None


@dataclass(frozen=True)
class Disposition:
    observation: Observation
    status: str
    quantity: int
    origin: ReadingOrigin
    decision: str

    @property
    def credential(self) -> SampleCredential:
        return self.observation.credential


def apply_disposition(observation: Observation, previous: Disposition | None,
                      operation: QualityOperation) -> Disposition:
    status = previous.status if previous is not None else 'pending'
    transitions = {
        'approve': {'pending': 'billable'},
        'hold': {'billable': 'held'},
        'release': {'held': 'billable'},
        'correct': {'billable': 'billable', 'held': 'held'},
    }
    successor = transitions.get(operation.action, {}).get(status)
    if successor is None:
        raise InvalidQualityOperation(f'{operation.action} cannot be applied to {status}')
    supplied_correction = operation.quantity is not None or operation.origin is not None
    if supplied_correction and operation.action not in ('correct', 'release'):
        raise InvalidQualityOperation('corrections require a correct or release operation')
    if supplied_correction or operation.action == 'correct':
        if type(operation.quantity) is not int:
            raise InvalidQualityOperation('a correction requires an integer reading')
        validate_origin(operation.origin)
        quantity, origin = operation.quantity, operation.origin
    else:
        reading = previous if previous is not None else observation
        quantity, origin = reading.quantity, reading.origin
    return Disposition(observation, successor, quantity, origin, operation.decision)
