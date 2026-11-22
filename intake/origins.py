from dataclasses import dataclass


@dataclass(frozen=True)
class ReadingOrigin:
    document: str
    line: int


def is_label(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def valid_index(value: object, minimum: int = 1) -> bool:
    return type(value) is int and minimum <= value <= 2**63 - 1


def validate_origin(origin: ReadingOrigin) -> None:
    if (not isinstance(origin, ReadingOrigin) or not is_label(origin.document)
            or not valid_index(origin.line)):
        raise ValueError('an origin needs a named document and a positive line number')


def origin_record(origin: ReadingOrigin) -> list:
    return [origin.document, origin.line]


def origin_from_record(record: list) -> ReadingOrigin:
    if not isinstance(record, list) or len(record) != 2:
        raise ValueError('invalid origin record')
    origin = ReadingOrigin(*record)
    validate_origin(origin)
    return origin
