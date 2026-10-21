"""LangSmith Dataset 업로드 테스트 (가짜 Client, dev와 대역 eval만 쓴다)."""

import traceback
from types import SimpleNamespace

import pytest

from app import data_loader
from app.config import get_settings
from app.data_loader import load_requests
from eval.data import load_split
from eval.tracing import api_key_present, project_name
from eval.upload_dataset import DatasetMismatchError, build_examples, dataset_name, main, upload_split
from tests._eval_guard import data_dir_with_stand_in_eval, real_eval_files_read, watch_reads


class FakeClient:
    def __init__(self, existing_ids: list[str] | None = None):
        self.existing_ids = existing_ids
        self.calls: list[str] = []
        self.created_examples: list[dict] = []

    def has_dataset(self, *, dataset_name):
        return self.existing_ids is not None

    def list_examples(self, *, dataset_name):
        return [SimpleNamespace(inputs={"request_id": rid}) for rid in self.existing_ids or []]

    def delete_dataset(self, *, dataset_name):
        self.calls.append(f"delete:{dataset_name}")
        self.existing_ids = None

    def create_dataset(self, name, *, description=None):
        self.calls.append(f"create:{name}")

    def create_examples(self, *, dataset_name, examples):
        self.calls.append(f"examples:{dataset_name}:{len(examples)}")
        self.created_examples = examples


DEV_IDS = [item.request_id for item in load_requests("dev")]


def test_예제는_마스킹한_본문과_정답_라벨_전체를_담는다():
    item = load_requests("dev")[0].model_copy(update={"text": "접속 password=hunter2 로 해주세요"})
    example = build_examples([item])[0]
    assert example["inputs"] == {
        "request_id": "REQ-D01",
        "submitted_at": "2026-10-29T16:00:00+09:00",
        "requester": "김운영",
        "text": "접속 password=**** 로 해주세요",
    }
    assert example["outputs"]["verdict"] == "반려" and example["outputs"]["violations"][0] == "CHG-01"
    assert example["outputs"]["structured"]["target"] == "payments-db"
    assert example["metadata"]["split"] == "dev" and "대표 시나리오" in example["metadata"]["tags"]


def test_없으면_Dataset을_만들고_예제를_올린다():
    client = FakeClient(existing_ids=None)
    assert upload_split(client, "dev", load_requests("dev")) == "uploaded"
    assert client.calls == ["create:gk-dev", "examples:gk-dev:10"]


def test_같은_요청_집합이_이미_있으면_건너뛴다():
    client = FakeClient(existing_ids=DEV_IDS)
    assert upload_split(client, "dev", load_requests("dev")) == "skipped"
    assert client.calls == []


def test_다른_집합이면_멈추고_force면_지우고_다시_만든다():
    client = FakeClient(existing_ids=["REQ-D01"])
    with pytest.raises(DatasetMismatchError, match=r"올릴 10건, 기존 1건"):
        upload_split(client, "dev", load_requests("dev"))
    assert client.calls == []
    assert upload_split(client, "dev", load_requests("dev"), force=True) == "uploaded"
    assert client.calls == ["delete:gk-dev", "create:gk-dev", "examples:gk-dev:10"]


def test_Dataset_이름():
    assert dataset_name("dev") == "gk-dev" and dataset_name("eval") == "gk-eval"


def test_dry_run은_Client를_만들지_않고_건수만_출력한다(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run은 LangSmith Client를 만들면 안 된다")

    monkeypatch.setattr("langsmith.Client", boom)
    assert main(["--split", "dev", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "gk-dev: 10건 (dry-run, 업로드하지 않음)"


def test_키가_없으면_업로드하지_않고_안내한다(monkeypatch, capsys):
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main(["--split", "dev"]) == 2
    assert "LANGCHAIN_API_KEY" in capsys.readouterr().err


def test_업로드_실행은_건수만_출력한다(monkeypatch, capsys):
    client = FakeClient(existing_ids=None)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "dummy-key")
    monkeypatch.setattr("langsmith.Client", lambda: client)
    assert main(["--split", "dev"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "gk-dev: 10건 업로드함"
    assert "dummy-key" not in out
    assert client.calls == ["create:gk-dev", "examples:gk-dev:10"]


def test_이미_있으면_건너뜀을_다르면_종료코드_1을_낸다(monkeypatch, capsys):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "dummy-key")
    monkeypatch.setattr("langsmith.Client", lambda: FakeClient(existing_ids=DEV_IDS))
    assert main(["--split", "dev"]) == 0
    assert "건너뜀" in capsys.readouterr().out
    monkeypatch.setattr("langsmith.Client", lambda: FakeClient(existing_ids=["x"]))
    assert main(["--split", "dev"]) == 1
    assert "--force" in capsys.readouterr().err


def test_eval_경로는_대역_파일로만_확인하고_본문을_출력하지_않는다(monkeypatch, tmp_path, capsys):
    source = get_settings().data_dir
    stand_in = data_dir_with_stand_in_eval(source, tmp_path / "data")
    monkeypatch.setenv("GK_DATA_DIR", str(stand_in))
    data_loader.clear_caches()
    opened = watch_reads(monkeypatch)
    assert main(["--split", "all", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == ["gk-dev: 10건 (dry-run, 업로드하지 않음)", "gk-eval: 10건 (dry-run, 업로드하지 않음)"]
    assert stand_in / "eval/requests_eval.json" in opened  # 대역 파일은 읽었고
    assert real_eval_files_read(opened, source) == []  # 실제 정답 파일은 열지 않았다
    assert not (stand_in / "eval/label_review.md").exists()
    assert "payments" not in out


def test_정답셋_검증_오류_메시지에_본문이_실리지_않는다(monkeypatch, tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval/requests_dev.json").write_text(
        '[{"request_id": "REQ-X", "split": "dev", "submitted_at": "2026-10-29T16:00:00+09:00",'
        ' "requester": "누구", "text": "비밀본문SECRETBODY", "expected": {"verdict": "보류"}}]',
        encoding="utf-8",
    )
    monkeypatch.setenv("GK_DATA_DIR", str(tmp_path))
    data_loader.clear_caches()
    with pytest.raises(ValueError) as caught:
        load_split("dev")
    rendered = "".join(traceback.format_exception(caught.value))
    assert "SECRETBODY" not in rendered and "보류" not in rendered
    assert "dev 정답셋 검증 실패" in str(caught.value)


def test_추적_도우미(monkeypatch):
    from datetime import datetime

    assert project_name("v0", "dev", datetime(2026, 10, 21, 9, 5, 7)) == "gk-v0-dev-20261021-090507"
    for name in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert api_key_present() is False
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    assert api_key_present() is True
