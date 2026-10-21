"""규정 원문(change_policy.md)을 조항 단위로 자른다.

MCP Resource는 파일을 다시 쓰지 않고 읽어서 파싱한 결과를 그대로 내보낸다. 규정을 고치면 파일만
고치면 되고, 이 모듈이나 Resource 정의는 건드리지 않는다.
"""

import re
from dataclasses import dataclass

from app.data_loader import load_policy_text

_CLAUSE_HEADING = re.compile(r"^### (CHG-\d{2}) (.+)$", re.MULTILINE)
_ANY_HEADING = re.compile(r"^#{1,3} ", re.MULTILINE)
_STANDARD_HEADING = re.compile(r"^## .*표준 변경 목록.*$", re.MULTILINE)
_H2_HEADING = re.compile(r"^## ", re.MULTILINE)
_KIND = re.compile(r"^- 구분: (.+)$", re.MULTILINE)
_APPLIES_TO = re.compile(r"^- 적용 대상: (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Clause:
    rule_id: str
    title: str
    kind: str  # "- 구분:" 값 (금지/보완/절차)
    applies_to: str  # "- 적용 대상:" 값
    text: str  # 제목 줄부터 본문 끝까지의 원문


@dataclass(frozen=True)
class PolicyDocs:
    clauses: dict[str, Clause]  # 문서에 나온 순서대로
    standard: str  # 표준 변경 목록 절 원문

    @property
    def index(self) -> str:
        """조항 목록. 파싱한 결과로 만들고 손으로 쓰지 않는다."""
        rows = [
            f"| {c.rule_id} | {c.title} | {c.kind} | {c.applies_to} | rules://{c.rule_id} |"
            for c in self.clauses.values()
        ]
        rows.append("| STD | 표준 변경 목록 (STD-01, STD-02) | - | - | rules://standard |")
        return "\n".join(
            [
                "# 변경관리 규정 조항 목록",
                "",
                "| 조항 | 제목 | 구분 | 적용 대상 | 원문 |",
                "|---|---|---|---|---|",
                *rows,
            ]
        )


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def split_policy(text: str) -> PolicyDocs:
    """규정 원문에서 `### CHG-xx` 조항들과 표준 변경 목록 절을 뽑는다."""
    matches = list(_CLAUSE_HEADING.finditer(text))
    if not matches:
        raise ValueError("규정 원문에서 CHG 조항을 찾지 못했다")

    clauses: dict[str, Clause] = {}
    for match in matches:
        rule_id = match.group(1)
        if rule_id in clauses:
            raise ValueError(f"조항이 중복되었다: {rule_id}")
        # 조항은 다음 제목(### 또는 ##)이 시작하기 전까지다
        following = _ANY_HEADING.search(text, match.end())
        end = following.start() if following else len(text)
        body = text[match.start() : end].strip()
        clauses[rule_id] = Clause(
            rule_id=rule_id,
            title=match.group(2).strip(),
            kind=_first(_KIND, body),
            applies_to=_first(_APPLIES_TO, body),
            text=body,
        )

    standard_match = _STANDARD_HEADING.search(text)
    if standard_match is None:
        raise ValueError("규정 원문에서 표준 변경 목록 절을 찾지 못했다")
    next_h2 = _H2_HEADING.search(text, standard_match.end())
    standard = text[standard_match.start() : next_h2.start() if next_h2 else len(text)].strip()

    return PolicyDocs(clauses=clauses, standard=standard)


def load_policy_docs() -> PolicyDocs:
    return split_policy(load_policy_text())
