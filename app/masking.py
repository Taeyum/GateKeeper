"""요청 본문의 비밀값 마스킹 (CLAUDE.md 절대 규칙 5).

LLM과 LangSmith로 본문을 넘기기 전에 거치는 순수 함수다. 4단계 하네스(v0 입력, Judge 입력,
Dataset 업로드)가 처음 쓰고, 7단계 API 미들웨어가 같은 함수를 재사용한다.

치환 규칙
    키=값, 키: 값 형태는 키를 남기고 값만 가린다. 어떤 키가 걸렸는지는 승인자가 알아야 한다.
    AWS 액세스 키와 PEM 블록은 통째로 가린다.
"""

import re

MASK = "****"

# 키 이름에 이 단어가 들어 있으면 비밀값 키로 본다 (password, db_password, api-key, access_token ...).
_SECRET_KEY = r"[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)[\w.-]*"

# 키 [=:] 값. 값은 따옴표로 감싸졌거나 공백/쉼표/세미콜론 전까지다.
_KEY_VALUE = re.compile(
    rf"(?P<key>{_SECRET_KEY})(?P<sep>\s*[=:]\s*)(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)


def mask_secrets(text: str) -> str:
    """본문에서 비밀값을 ****로 바꾼다. 비밀값이 없으면 입력을 그대로 돌려준다."""
    text = _PEM_BLOCK.sub(MASK, text)
    text = _AWS_ACCESS_KEY.sub(MASK, text)
    return _KEY_VALUE.sub(lambda m: f"{m.group('key')}{m.group('sep')}{MASK}", text)
