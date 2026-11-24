import re


def is_valid_group_number(value: str) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"[0-9]{3}-[0-9]{3}", value) is not None
    )
