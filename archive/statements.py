from dataclasses import dataclass

from intake.observations import SampleCredential, StationDay
from intake.origins import ReadingOrigin


@dataclass(frozen=True)
class ClosedAdjustment:
    adjustment_id: int
    credential: SampleCredential
    delta: int
    origin: ReadingOrigin


@dataclass(frozen=True)
class ClosedLine:
    credential: SampleCredential
    quantity: int
    origin: ReadingOrigin
    adjustments: tuple[ClosedAdjustment, ...]
    total: int


@dataclass(frozen=True)
class ClosedStatement:
    statement_id: int
    station_day: StationDay
    revision: int
    lines: tuple[ClosedLine, ...]
    total: int
