"""비밀값 마스킹 테스트."""

import pytest

from app.data_loader import load_requests
from app.masking import MASK, mask_secrets


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("db password=hunter2 로 접속", f"db password={MASK} 로 접속"),
        ("PASSWORD: hunter2", f"PASSWORD: {MASK}"),
        ("db_password = 's3 cret'", f"db_password = {MASK}"),
        ('api_key="abc123"', f"api_key={MASK}"),
        ("access-token=eyJhbGci.xyz, 다음 줄", f"access-token={MASK}, 다음 줄"),
        ("secret=abc;포트 5432", f"secret={MASK};포트 5432"),
    ],
)
def test_키_값_형태는_키를_남기고_값만_가린다(text, expected):
    assert mask_secrets(text) == expected


def test_AWS_액세스_키를_가린다():
    assert mask_secrets("키는 AKIAIOSFODNN7EXAMPLE 입니다") == f"키는 {MASK} 입니다"
    assert mask_secrets("임시 키 ASIAIOSFODNN7EXAMPLE") == f"임시 키 {MASK}"


def test_PEM_블록은_통째로_가린다():
    text = "인증서 교체\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----\n끝"
    assert mask_secrets(text) == f"인증서 교체\n{MASK}\n끝"


def test_비밀값이_없으면_그대로_돌려준다():
    text = "payments-db 5432 포트를 203.0.113.10/32에서만 열어주세요. token 발급 절차는 별도입니다."
    assert mask_secrets(text) == text


def test_dev_요청_본문은_오탐_없이_그대로_나온다():
    for item in load_requests("dev"):
        assert mask_secrets(item.text) == item.text, item.request_id
