"""에이전트 그래프의 오류."""


class ReviewIncomplete(RuntimeError):
    """자동 검토를 끝까지 하지 못해 판정을 만들지 않는다.

    요청 대상이 CMDB에 없거나 규정 검사 결과를 얻지 못한 경우처럼, 판정을 지어내는 것보다 사람에게
    넘기는 쪽이 맞을 때 낸다. 메시지에는 요청 본문에서 온 값을 싣지 않는다(평가 하네스는 타입 이름만 남기고,
    API는 "수동 검토로 넘깁니다"로 바꾼다).

    stage와 detail은 왜 멈췄는지 찾으려는 진단용이다. stage는 어느 노드에서 멈췄는지(본문 값 없음)이고,
    detail에는 대상 값, 작업 구간, 하위 오류 종류처럼 요청에서 온 값이 들어갈 수 있다. 그래서 평가 하네스가
    dev split의 기록에만 남기고, eval split과 API 응답에는 내보내지 않는다 (CLAUDE.md 절대 규칙 1).
    """

    def __init__(self, message: str, *, stage: str | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.detail = detail

    def diagnosis(self) -> str | None:
        """stage와 detail을 한 문장으로. 둘 다 없으면 None."""
        parts = [part for part in (self.stage, self.detail) if part]
        return ": ".join(parts) if parts else None
