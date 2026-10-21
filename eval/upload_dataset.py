"""정답셋을 LangSmith Dataset(gk-dev, gk-eval)으로 올린다.

    python eval/upload_dataset.py --split dev
    python eval/upload_dataset.py --split eval     # Claude Code 밖의 별도 터미널에서 사용자가 실행한다

출력은 Dataset 이름과 건수뿐이다. eval 정답셋은 본문을 화면에 내지 않는다 (CLAUDE.md 절대 규칙 1).
요청 본문은 마스킹한 뒤 올린다 (절대 규칙 5). 이미 같은 요청 ID 집합이 올라가 있으면 건너뛰고, 다르면
--force 없이는 멈춘다.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.masking import mask_secrets  # noqa: E402
from app.schemas import LabeledRequest  # noqa: E402
from eval.data import load_split  # noqa: E402
from eval.tracing import api_key_present  # noqa: E402


class DatasetMismatchError(RuntimeError):
    """Dataset에 이미 다른 요청 집합이 올라가 있다."""


def dataset_name(split: str) -> str:
    return f"gk-{split}"


def build_examples(items: list[LabeledRequest]) -> list[dict]:
    """LangSmith 예제로 바꾼다. 입력은 요청(본문은 마스킹), 출력은 정답 라벨 전체다."""
    return [
        {
            "inputs": {
                "request_id": item.request_id,
                "submitted_at": item.submitted_at.isoformat(),
                "requester": item.requester,
                "text": mask_secrets(item.text),
            },
            "outputs": item.expected.model_dump(mode="json"),
            "metadata": {"split": item.split, "tags": item.tags},
        }
        for item in items
    ]


def upload_split(client, split: str, items: list[LabeledRequest], *, force: bool = False) -> str:
    """Dataset 하나를 올리고 "uploaded" 또는 "skipped"를 돌려준다."""
    name = dataset_name(split)
    examples = build_examples(items)
    wanted = {example["inputs"]["request_id"] for example in examples}
    if client.has_dataset(dataset_name=name):
        existing = {example.inputs.get("request_id") for example in client.list_examples(dataset_name=name)}
        if existing == wanted:
            return "skipped"
        if not force:
            raise DatasetMismatchError(
                f"{name}에 이미 다른 예제가 있다 (올릴 {len(wanted)}건, 기존 {len(existing)}건). 다시 만들려면 --force."
            )
        client.delete_dataset(dataset_name=name)
    client.create_dataset(name, description=f"GateKeeper 변경요청 정답셋 ({split})")
    client.create_examples(dataset_name=name, examples=examples)
    return "uploaded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="정답셋을 LangSmith Dataset으로 올린다.")
    parser.add_argument("--split", choices=["dev", "eval", "all"], default="dev")
    parser.add_argument("--dry-run", action="store_true", help="업로드하지 않고 건수만 확인한다")
    parser.add_argument("--force", action="store_true", help="다른 내용이 올라가 있으면 지우고 다시 만든다")
    args = parser.parse_args(argv)

    get_settings()  # .env를 환경 변수로 읽어 둔다 (LangSmith 키)
    splits = ["dev", "eval"] if args.split == "all" else [args.split]
    client = None
    if not args.dry_run:
        if not api_key_present():
            print("LANGCHAIN_API_KEY(또는 LANGSMITH_API_KEY)가 없다. .env에 넣고 다시 실행한다.", file=sys.stderr)
            return 2
        from langsmith import Client

        client = Client()

    for split in splits:
        items = load_split(split)
        if client is None:
            print(f"{dataset_name(split)}: {len(items)}건 (dry-run, 업로드하지 않음)")
            continue
        try:
            status = upload_split(client, split, items, force=args.force)
        except DatasetMismatchError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        label = "이미 올라가 있어 건너뜀" if status == "skipped" else "업로드함"
        print(f"{dataset_name(split)}: {len(items)}건 {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
