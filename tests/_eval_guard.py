"""eval split 테스트 장치 (CLAUDE.md 절대 규칙 1).

테스트는 실제 eval 정답 파일을 열지 않는다. eval 경로를 검증해야 할 때는 dev 내용을 eval로 바꿔 쓴 대역
파일을 임시 데이터 디렉터리에 만들어 쓰고, 경로 접근 감시로 실제 파일이 열리지 않았음을 확인한다.
"""

import json
import shutil
from pathlib import Path

REAL_EVAL_FILES = ("requests_eval.json", "label_review.md")


def watch_reads(monkeypatch) -> list[Path]:
    """Path.read_text/read_bytes로 읽은 파일의 전체 경로를 기록한다 (data_loader와 해시 계산이 쓰는 경로)."""
    opened: list[Path] = []
    real_text, real_bytes = Path.read_text, Path.read_bytes

    def text_spy(self, *args, **kwargs):
        opened.append(Path(self))
        return real_text(self, *args, **kwargs)

    def bytes_spy(self, *args, **kwargs):
        opened.append(Path(self))
        return real_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", text_spy)
    monkeypatch.setattr(Path, "read_bytes", bytes_spy)
    return opened


def real_eval_files_read(opened: list[Path], real_data_dir: Path) -> list[Path]:
    """실제 데이터 디렉터리의 eval 정답 파일이 읽혔다면 그 경로들. 비어 있어야 한다."""
    real = {(real_data_dir / "eval" / name).resolve() for name in REAL_EVAL_FILES}
    return [path for path in opened if path.resolve() in real]


def data_dir_with_stand_in_eval(source: Path, target: Path) -> Path:
    """source 데이터 디렉터리를 복사하되 실제 eval 정답 파일은 복사하지 않고, dev를 eval로 바꾼 대역을 둔다."""
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*REAL_EVAL_FILES))
    items = json.loads((source / "eval/requests_dev.json").read_text(encoding="utf-8"))
    for item in items:
        item["split"] = "eval"
        item["request_id"] = item["request_id"].replace("-D", "-E")
    (target / "eval/requests_eval.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return target
