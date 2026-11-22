from dataclasses import dataclass
from datetime import date

from intake.origins import ReadingOrigin, is_label, valid_index


@dataclass(frozen=True)
class StationDay:
    station: str
    day: str

    @property
    def key(self) -> tuple[str, str]:
        return self.station, self.day


@dataclass(frozen=True)
class SampleCredential:
    station_day: StationDay
    batch_sequence: int
    position: int
    number: str

    @property
    def order(self) -> tuple[int, int]:
        return self.batch_sequence, self.position


@dataclass(frozen=True)
class BatchReceipt:
    station_day: StationDay
    name: str
    sequence: int
    credentials: tuple[SampleCredential, ...]


@dataclass(frozen=True)
class Observation:
    credential: SampleCredential
    quantity: int
    origin: ReadingOrigin


@dataclass(frozen=True)
class SampleInput:
    number: str
    quantity: int
    unit: str
    origin: ReadingOrigin


def validate_station_day(station_day: StationDay) -> None:
    if not isinstance(station_day, StationDay) or not is_label(station_day.station):
        raise ValueError('a station name is required')
    if not isinstance(station_day.day, str):
        raise ValueError('a calendar date in YYYY-MM-DD form is required')
    if date.fromisoformat(station_day.day).isoformat() != station_day.day:
        raise ValueError('use YYYY-MM-DD for the station day')


def validate_credential(credential: SampleCredential) -> None:
    if not isinstance(credential, SampleCredential):
        raise ValueError('an original-sample credential is required')
    validate_station_day(credential.station_day)
    if (not all(valid_index(index) for index in credential.order)
            or not is_label(credential.number)):
        raise ValueError('invalid original-sample credential')


def credential_record(credential: SampleCredential) -> list:
    return [*credential.station_day.key, *credential.order, credential.number]


def credential_from_record(record: list) -> SampleCredential:
    if not isinstance(record, list) or len(record) != 5:
        raise ValueError('invalid credential record')
    station, day, batch, position, number = record
    credential = SampleCredential(StationDay(station, day), batch, position, number)
    validate_credential(credential)
    return credential


def quantity_record(quantity: int) -> str:
    if type(quantity) is not int:
        raise ValueError('quantity must be an integer')
    return hex(quantity)


def quantity_from_record(record: str) -> int:
    if not isinstance(record, str):
        raise ValueError('invalid integer record')
    quantity = int(record, 16)
    if hex(quantity) != record:
        raise ValueError('noncanonical integer record')
    return quantity
