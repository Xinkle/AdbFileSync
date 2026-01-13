# snapshot_store.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List


Meta = Tuple[int, int]  # (mtime, size)


@dataclass(frozen=True)
class SnapshotEntry:
    local: Optional[Meta]
    device: Optional[Meta]


Snapshot = Dict[str, SnapshotEntry]


@dataclass(frozen=True)
class DeletionCandidate:
    """
    local_missing=True  => 로컬에서 삭제된 것으로 추정(이전엔 있었고 지금은 로컬이 없음, 디바이스는 있음)
    local_missing=False => 디바이스에서 삭제된 것으로 추정(이전엔 있었고 지금은 디바이스가 없음, 로컬은 있음)
    """
    rel: str
    local_missing: bool


class SnapshotStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Optional[Snapshot]:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            data = raw.get("files", {})
            snap: Snapshot = {}
            for rel, ent in data.items():
                l = ent.get("local")
                d = ent.get("device")
                snap[rel] = SnapshotEntry(
                    local=tuple(l) if isinstance(l, list) and len(l) == 2 else None,
                    device=tuple(d) if isinstance(d, list) and len(d) == 2 else None,
                )
            return snap
        except Exception:
            return None

    def save(self, local_map: Dict[str, Meta], dev_map: Dict[str, Meta]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        all_paths = sorted(set(local_map.keys()) | set(dev_map.keys()))
        out: Dict[str, dict] = {}
        for rel in all_paths:
            out[rel] = {
                "local": list(local_map[rel]) if rel in local_map else None,
                "device": list(dev_map[rel]) if rel in dev_map else None,
            }

        payload = {
            "version": 1,
            "files": out,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def compute_deletions(prev: Snapshot, local_map: Dict[str, Meta], dev_map: Dict[str, Meta]) -> List[DeletionCandidate]:
        """
        삭제 후보:
          - 이전 스냅샷에 local+device 둘 다 존재했고
          - 현재는 한쪽만 존재하면
            => 사라진 쪽에서 삭제로 추정
        """
        cands: List[DeletionCandidate] = []

        for rel, ent in prev.items():
            had_local = ent.local is not None
            had_dev = ent.device is not None
            if not (had_local and had_dev):
                continue  # 양쪽에 있었던 이력이 없는 파일은 삭제 판단 안 함

            now_local = rel in local_map
            now_dev = rel in dev_map

            if now_local and now_dev:
                continue
            if (not now_local) and (not now_dev):
                continue

            if (not now_local) and now_dev:
                cands.append(DeletionCandidate(rel=rel, local_missing=True))
            elif now_local and (not now_dev):
                cands.append(DeletionCandidate(rel=rel, local_missing=False))

        return cands