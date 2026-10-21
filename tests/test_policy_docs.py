import pytest

from app.data_loader import load_policy_text, load_rules
from app.mcp_server.policy_docs import load_policy_docs, split_policy

# rules.json의 severity와 change_policy.md의 "구분"은 같은 분류다
KIND_BY_SEVERITY = {"forbidden": "금지", "fixable": "보완", "procedure": "절차"}


def test_조항_10개를_문서_순서대로_뽑는다():
    docs = load_policy_docs()
    assert list(docs.clauses) == [f"CHG-{i:02d}" for i in range(1, 11)]
    assert docs.clauses["CHG-01"].title == "배포 금지 시간대"


def test_조항_ID가_rules_json과_같다():
    docs = load_policy_docs()
    assert list(docs.clauses) == [r.rule_id for r in load_rules().rules]


def test_구분과_적용_대상을_읽는다():
    docs = load_policy_docs()
    for rule in load_rules().rules:
        assert docs.clauses[rule.rule_id].kind == KIND_BY_SEVERITY[rule.severity], rule.rule_id
    assert docs.clauses["CHG-03"].applies_to.startswith("방화벽")


def test_CHG_03_조각에는_그_조항만_들어_있다():
    text = load_policy_docs().clauses["CHG-03"].text
    assert text.startswith("### CHG-03 DB 포트 외부 노출 금지")
    assert "5432" in text and "파트너" in text
    assert "CHG-04 롤백" not in text and "### CHG-02" not in text


def test_조각은_원문의_일부이고_다시_쓰지_않았다():
    policy = load_policy_text()
    docs = load_policy_docs()
    assert all(c.text in policy for c in docs.clauses.values())
    assert docs.standard in policy


def test_마지막_조항은_다음_절을_포함하지_않는다():
    text = load_policy_docs().clauses["CHG-10"].text
    assert "동결 기간" in text
    assert "STD-01" not in text and "## 3." not in text


def test_표준_변경_절():
    standard = load_policy_docs().standard
    assert standard.startswith("## 3. 표준 변경 목록")
    assert "STD-01" in standard and "STD-02" in standard
    assert "CHG-06, CHG-10만 적용" in standard
    assert "## 4." not in standard and "### CHG-10" not in standard


def test_index는_파싱_결과로_만든다():
    index = load_policy_docs().index
    for i in range(1, 11):
        assert f"rules://CHG-{i:02d}" in index
    assert "rules://standard" in index
    assert "| CHG-03 | DB 포트 외부 노출 금지 | 금지 |" in index


MINI = """# 규정
## 2. 조항
### CHG-01 첫째
- 구분: 금지
- 적용 대상: 방화벽
내용 하나
### CHG-02 둘째
내용 둘
## 3. 표준 변경 목록
STD-01 표
## 4. 기타
"""


def test_작은_문서로_경계를_확인한다():
    docs = split_policy(MINI)
    assert docs.clauses["CHG-01"].text.endswith("내용 하나")
    assert docs.clauses["CHG-02"].kind == "" and docs.clauses["CHG-02"].text.endswith("내용 둘")
    assert docs.standard == "## 3. 표준 변경 목록\nSTD-01 표"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# 규정\n## 3. 표준 변경 목록\n표", "CHG 조항"),
        ("### CHG-01 가\n### CHG-01 나\n## 3. 표준 변경 목록\n표", "중복"),
        ("### CHG-01 가\n내용", "표준 변경 목록"),
    ],
)
def test_구조가_깨진_문서는_거부한다(text, message):
    with pytest.raises(ValueError, match=message):
        split_policy(text)
