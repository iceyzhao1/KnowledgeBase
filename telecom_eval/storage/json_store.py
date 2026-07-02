import json
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel

from telecom_eval.models.cases import EvaluationCase, GoldStatus

T = TypeVar("T", bound=BaseModel)


class JsonEvaluationStore:
    """第一版本地文件资产库：cases / traces / artifacts / metrics / reports。

    全部按 UTF-8 读写，``ensure_ascii=False`` 保留中文原文。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.cases_dir = self.root / "cases"
        self.traces_dir = self.root / "traces"
        self.artifacts_dir = self.root / "artifacts"
        self.metrics_dir = self.root / "metrics"
        self.reports_dir = self.root / "reports"
        for directory in [
            self.cases_dir,
            self.traces_dir,
            self.artifacts_dir,
            self.metrics_dir,
            self.reports_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def _write_model(self, path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    def _read_model(self, path: Path, model_type: type[T]) -> T:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def save_case(self, case: EvaluationCase) -> None:
        self._write_model(self.cases_dir / f"{case.case_id}.json", case)

    def load_case(self, case_id: str) -> EvaluationCase:
        return self._read_model(self.cases_dir / f"{case_id}.json", EvaluationCase)

    def list_cases(
        self, *, dataset_id: str | None = None, confirmed_only: bool = False
    ) -> list[EvaluationCase]:
        cases = [
            self._read_model(path, EvaluationCase)
            for path in sorted(self.cases_dir.glob("*.json"))
        ]
        if dataset_id is not None:
            cases = [case for case in cases if case.dataset_id == dataset_id]
        if confirmed_only:
            cases = [case for case in cases if case.gold_status is GoldStatus.CONFIRMED]
        return cases

    def write_json(self, namespace: str, object_id: str, payload: dict) -> Path:
        path = self.root / namespace / f"{object_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_json(self, namespace: str, object_id: str) -> dict:
        path = self.root / namespace / f"{object_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def append_jsonl(self, namespace: str, object_id: str, rows: Iterable[dict]) -> Path:
        path = self.root / namespace / f"{object_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
