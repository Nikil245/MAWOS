"""Explicit room-kind compatibility rules shared by planning and validation."""


# A computer lab can host an ordinary lecture when it has enough seats. The
# reverse is deliberately not true: a classroom cannot satisfy a lab request.
_COMPATIBLE_KINDS = {
    "classroom": frozenset({"classroom", "computer_lab"}),
}


def room_kind_satisfies(required: str, actual: str) -> bool:
    if required == "any":
        return True
    return actual in _COMPATIBLE_KINDS.get(required, frozenset({required}))
