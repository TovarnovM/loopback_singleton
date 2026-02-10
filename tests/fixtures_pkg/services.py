class TestCounter:
    __test__ = False
    def __init__(self, start: int = 0, step: int = 1) -> None:
        self._value = start
        self._step = step

    def inc(self) -> int:
        self._value += self._step
        return self._value

    def ping(self) -> str:
        return "pong"

    def fail(self) -> None:
        raise RuntimeError("boom")


def make_counter(start: int = 0, step: int = 1) -> TestCounter:
    return TestCounter(start=start, step=step)
