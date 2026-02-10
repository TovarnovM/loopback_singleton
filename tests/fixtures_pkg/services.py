class TestCounter:
    def __init__(self) -> None:
        self._value = 0

    def inc(self) -> int:
        self._value += 1
        return self._value

    def ping(self) -> str:
        return "pong"

    def fail(self) -> None:
        raise RuntimeError("boom")
