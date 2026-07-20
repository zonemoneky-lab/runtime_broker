from __future__ import annotations


def compare_versions(left: str, right: str) -> int:
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    if left_parts and right_parts:
        max_len = max(len(left_parts), len(right_parts))
        padded_left = left_parts + [0] * (max_len - len(left_parts))
        padded_right = right_parts + [0] * (max_len - len(right_parts))
        return (padded_left > padded_right) - (padded_left < padded_right)
    if left_parts and not right_parts:
        return 1
    if right_parts and not left_parts:
        return -1
    return (left > right) - (left < right)


def _version_parts(value: str) -> list[int]:
    cleaned = value.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.replace("-", ".").split("."):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            return []
    return parts
