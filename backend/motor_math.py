"""Hardware independent differential-drive and encoder calculations."""

def unwrap_delta(current: int, previous: int, modulus: int = 4096) -> int:
    return (current - previous + modulus // 2) % modulus - modulus // 2


def wheel_speeds(linear: float, angular: float, separation: float) -> tuple[float, float]:
    half = separation / 2
    return linear - angular * half, linear + angular * half


def raw_speed(mps: float, sign: int, max_linear: float, raw_limit: int) -> int:
    value = round(mps / max(max_linear, .001) * raw_limit)
    return max(-raw_limit, min(raw_limit, value)) * sign
