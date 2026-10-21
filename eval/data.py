"""평가 스크립트가 쓰는 정답셋 로더.

load_requests 위에 한 겹을 얹어서, 라벨 검증이 실패해도 요청 본문이 오류 메시지로 새지 않게 한다
(CLAUDE.md 절대 규칙 1). Pydantic ValidationError의 메시지에는 input_value로 본문이 실린다.
"""

from pydantic import ValidationError

from app.data_loader import Split, load_requests
from app.schemas import LabeledRequest


def load_split(split: Split) -> list[LabeledRequest]:
    """eval은 평가 스크립트(run_eval, upload_dataset)에서만 열기 때문에 여기서 allow_eval을 켠다."""
    try:
        return load_requests(split, allow_eval=(split == "eval"))
    except ValidationError as exc:
        # 위치와 오류 종류만 남긴다. from None으로 원래 예외(입력값 포함)가 트레이스백에 찍히지 않게 한다.
        problems = [
            "/".join(str(part) for part in err["loc"]) + f": {err['type']}"
            for err in exc.errors(include_input=False, include_url=False)
        ]
        raise ValueError(f"{split} 정답셋 검증 실패 ({len(problems)}건): " + "; ".join(problems[:10])) from None
