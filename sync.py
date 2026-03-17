#!/usr/bin/env python3
# sync.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, List, Optional, Set

import adb_helper
from config_store import ConfigStore
from snapshot_store import SnapshotStore, DeletionCandidate
from ignore_manager import GitIgnoreManager

# adb_helper.py에서 이미 device-side prune(.obsidian/.trash)을 수행하므로,
# 여기서는 local-side만 동일 규칙으로 제외하면 충분합니다.
IGNORE_DIR_NAMES = {".obsidian", ".trash"}

Meta = Tuple[int, int]  # (mtime, size)


# {rel_path: (mtime, size)}
def _local_list_files_meta(local_root: Path) -> Dict[str, Meta]:
    result: Dict[str, Meta] = {}
    ignore_manager = GitIgnoreManager(local_root)
    
    for p in local_root.rglob("*"):
        if not p.is_file():
            continue

        rel = p.relative_to(local_root).as_posix()

        # 1. ignore .obsidian / .trash and their descendants (legacy rule)
        first = rel.split("/", 1)[0]
        if first in IGNORE_DIR_NAMES:
            continue
            
        # 2. ignore by .gitignore rules (new feature)
        if ignore_manager.is_ignored(p):
            continue

        try:
            st = p.stat()
            result[rel] = (int(st.st_mtime), int(st.st_size))
        except Exception:
            continue
    return result


def _format_plan_changed_only(
    local_map: Dict[str, Meta],
    dev_map: Dict[str, Meta],
    deletions: List[DeletionCandidate],
    is_initial_sync: bool = False,
) -> str:
    """
    기본 변경 출력(동일은 출력 안 함):
      - local only:      "{rel} > -비어 있음-"   (push)
      - device only:     "-비어 있음- < {rel}"   (pull)
      - both exist:
          - mtime diff:  "{rel} > {rel}" or "{rel} < {rel}"
          - mtime same:
              - size diff: "{rel} ! {rel}"   (conflict)

    삭제 후보는 별도 섹션으로 출력(항상 사용자 확인 필요).
    """
    if is_initial_sync:
        if not local_map and dev_map:
            return f"최초 동기화 감지 (로컬 비어 있음). \n기기에서 {len(dev_map)}개의 파일을 가져옵니다(PULL).\n"
        if local_map and not dev_map:
            return f"최초 동기화 감지 (기기 비어 있음). \n로컬에서 {len(local_map)}개의 파일을 보냅니다(PUSH).\n"

    lines: List[str] = ["로컬 - 디바이스"]
    all_paths = sorted(set(local_map.keys()) | set(dev_map.keys()))

    deletion_set_local_missing = {d.rel for d in deletions if d.local_missing}
    deletion_set_dev_missing = {d.rel for d in deletions if not d.local_missing}

    for rel in all_paths:
        if rel in deletion_set_local_missing or rel in deletion_set_dev_missing:
            continue

        in_l = rel in local_map
        in_d = rel in dev_map

        if in_l and not in_d:
            lines.append(f"{rel} > -비어 있음-")
            continue
        if not in_l and in_d:
            lines.append(f"-비어 있음- < {rel}")
            continue

        (lm, ls) = local_map[rel]
        (dm, ds) = dev_map[rel]

        if lm > dm:
            lines.append(f"{rel} > {rel}")   # local newer -> push
        elif lm < dm:
            lines.append(f"{rel} < {rel}")   # device newer -> pull
        else:
            if ls != ds:
                lines.append(f"{rel} ! {rel}")  # conflict

    if deletions:
        lines.append("")
        lines.append("삭제 후보 (확인 필요)")
        for d in deletions:
            if d.local_missing:
                lines.append(f"[로컬에서 삭제됨?] {d.rel}  (기기에서도 삭제하시겠습니까? 아니면 로컬로 복구?)")
            else:
                lines.append(f"[기기에서 삭제됨?] {d.rel}  (로컬에서도 삭제하시겠습니까? 아니면 기기로 복구?)")

    if len(lines) == 1:
        return "로컬 - 디바이스\n(변경 사항 없음)\n"
    return "\n".join(lines) + "\n"


def build_sync_plan_once(
    serial: str,
    device_dir: str,
    local_dir: Path,
    snapshot_path: Path,
):
    local_dir.mkdir(parents=True, exist_ok=True)
    local_map = _local_list_files_meta(local_dir)
    dev_map = adb_helper.adb_list_files_meta(serial, device_dir)

    store = SnapshotStore(snapshot_path)
    prev = store.load()
    deletions: List[DeletionCandidate] = []
    is_initial_sync = False

    if prev is not None:
        deletions = SnapshotStore.compute_deletions(prev, local_map, dev_map)
    else:
        if (not local_map and dev_map) or (local_map and not dev_map):
            is_initial_sync = True

    plan_text = _format_plan_changed_only(local_map, dev_map, deletions, is_initial_sync=is_initial_sync)
    return plan_text, local_map, dev_map, deletions


@dataclass(frozen=True)
class Action:
    kind: str
    rel: str


def _compute_actions(
    local_map: Dict[str, Meta],
    dev_map: Dict[str, Meta],
    deletions: List[DeletionCandidate],
) -> List[Action]:
    deletion_local_missing: Set[str] = {d.rel for d in deletions if d.local_missing}
    deletion_dev_missing: Set[str] = {d.rel for d in deletions if not d.local_missing}

    actions: List[Action] = []
    all_paths = sorted(set(local_map.keys()) | set(dev_map.keys()) | deletion_local_missing | deletion_dev_missing)

    for rel in all_paths:
        if rel in deletion_local_missing:
            actions.append(Action("local_deleted", rel))
            continue
        if rel in deletion_dev_missing:
            actions.append(Action("device_deleted", rel))
            continue

        in_l = rel in local_map
        in_d = rel in dev_map

        if in_l and not in_d:
            actions.append(Action("push", rel))
            continue
        if not in_l and in_d:
            actions.append(Action("pull", rel))
            continue
        if not in_l and not in_d:
            continue

        (lm, ls) = local_map[rel]
        (dm, ds) = dev_map[rel]

        if lm > dm:
            actions.append(Action("push", rel))
        elif lm < dm:
            actions.append(Action("pull", rel))
        else:
            if ls != ds:
                actions.append(Action("conflict", rel))

    return actions


def _ensure_device_parent_dir(serial: str, device_path: str) -> None:
    parent = device_path.rsplit("/", 1)[0] if "/" in device_path else device_path
    if not parent:
        return
    parent_q = adb_helper.sh_quote(parent)
    adb_helper.run(["adb", "-s", serial, "shell", f"mkdir -p {parent_q}"], timeout=20)


def _ensure_local_parent_dir(local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)


def _adb_push(serial: str, local_path: Path, device_path: str) -> None:
    _ensure_device_parent_dir(serial, device_path)
    adb_helper.run(["adb", "-s", serial, "push", "-a", str(local_path), device_path], timeout=180)


def _adb_pull(serial: str, device_path: str, local_path: Path, device_mtime: Optional[int] = None) -> None:
    _ensure_local_parent_dir(local_path)
    adb_helper.run(["adb", "-s", serial, "pull", device_path, str(local_path)], timeout=180)

    if device_mtime is not None:
        try:
            os.utime(local_path, (device_mtime, device_mtime))
        except Exception:
            pass


def _adb_delete_device_file(serial: str, device_path: str) -> None:
    q = adb_helper.sh_quote(device_path)
    adb_helper.run(["adb", "-s", serial, "shell", f"rm -f {q}"], timeout=30)


def _delete_local_file(local_path: Path) -> None:
    try:
        local_path.unlink()
    except FileNotFoundError:
        pass


def _prompt_yes_no(prompt: str) -> bool:
    ans = input(prompt).strip().lower()
    return ans in ("y", "yes", "ㅇ", "ㅇㅇ")


def _choose_conflict_policy_per_file(rel: str, remembered: Optional[str]) -> str:
    if remembered in ("local", "device", "skip"):
        return remembered

    while True:
        print(f"\n⚠️  충돌 발생: {rel}")
        print("유지할 버전을 선택하세요:")
        print("  L) 로컬 파일 유지 (로컬 -> 기기로 전송)")
        print("  D) 기기 파일 유지 (기기 -> 로컬로 전송)")
        print("  S) 건너뛰기 (아무 작업도 하지 않음)")
        print("  A) 이후 모든 충돌에 대해 현재 선택 적용 (한 번 더 확인)")
        choice = input("선택 [L/D/S/A]: ").strip().lower()

        if choice in ("l", "local"):
            return "local"
        if choice in ("d", "device"):
            return "device"
        if choice in ("s", "skip"):
            return "skip"
        if choice == "a":
            while True:
                g = input("나머지 모든 충돌에 어떤 정책을 적용할까요? [L/D/S]: ").strip().lower()
                if g in ("l", "local"):
                    return "local_apply_all"
                if g in ("d", "device"):
                    return "device_apply_all"
                if g in ("s", "skip"):
                    return "skip_apply_all"
                print("잘못된 선택입니다. L/D/S 중 하나를 선택하세요.")
        print("잘못된 선택입니다. L/D/S/A 중 하나를 선택하세요.")


def _device_path_join(root: str, rel: str) -> str:
    root = root.rstrip("/")
    rel = rel.lstrip("/")
    return f"{root}/{rel}" if rel else root


def main(store: Optional[ConfigStore] = None) -> int:
    if store is None:
        store = ConfigStore.default()
        
    sync_cfg = store.get_sync_config()

    if sync_cfg is None:
        print("동기화 설정이 초기화되지 않았습니다. 기기 및 폴더 선택을 먼저 실행해주세요.")
        return 2

    serial = sync_cfg.device.serial
    device_dir = sync_cfg.device_sync_dir
    local_dir = Path(sync_cfg.local_sync_dir)

    if not adb_helper.adb_available():
        print("adb를 실행할 수 없습니다. PATH 설정을 확인하세요.")
        return 2

    serials = adb_helper.list_connected_serials()
    if serial not in serials:
        print(f"기기가 연결되지 않았거나 승인되지 않았습니다: {serial}")
        print(f"연결된 기기: {serials}")
        return 3

    snapshot_path = store.path.parent / (store.path.name + ".snapshot")

    print(f"기기     : {serial} ({sync_cfg.device.model})")
    print(f"기기 경로 : {device_dir}")
    print(f"로컬 경로 : {local_dir}")
    print(f"스냅샷   : {snapshot_path}\n")

    plan_text, local_map, dev_map, deletions = build_sync_plan_once(
        serial, device_dir, local_dir, snapshot_path
    )

    print(plan_text)
    print(f"로컬 파일 수 : {len(local_map)}")
    print(f"기기 파일 수 : {len(dev_map)}")
    print(f"삭제 후보 수 : {len(deletions)}")

    actions = _compute_actions(local_map, dev_map, deletions)
    if not actions:
        print("\n변경 사항이 없습니다.")
        SnapshotStore(snapshot_path).save(local_map, dev_map)
        return 0

    pushes = sum(1 for a in actions if a.kind == "push")
    pulls = sum(1 for a in actions if a.kind == "pull")
    conflicts = sum(1 for a in actions if a.kind == "conflict")
    del_local = sum(1 for a in actions if a.kind == "device_deleted")
    del_dev = sum(1 for a in actions if a.kind == "local_deleted")

    print("\n요약:")
    print(f"  보내기(push)   : {pushes}")
    print(f"  가져오기(pull)  : {pulls}")
    print(f"  충돌(conflict) : {conflicts}")
    print(f"  로컬 삭제됨?    : {del_dev}  (기기에서 삭제하거나 로컬로 복구)")
    print(f"  기기 삭제됨?    : {del_local} (로컬에서 삭제하거나 기기로 복구)")

    if not _prompt_yes_no("\n동기화(전송/삭제)를 진행하시겠습니까? (y/N): "):
        print("중단되었습니다.")
        return 0

    remembered_conflict_policy: Optional[str] = None

    for a in actions:
        rel = a.rel
        device_path = _device_path_join(device_dir, rel)
        local_path = local_dir / Path(rel)

        try:
            if a.kind == "push":
                if not local_path.exists():
                    print(f"[건너뜀] 로컬 파일 없음: {rel}")
                    continue
                print(f"[보내기] {rel}")
                _adb_push(serial, local_path, device_path)

            elif a.kind == "pull":
                print(f"[가져오기] {rel}")
                device_mtime = dev_map.get(rel, (None, None))[0]
                _adb_pull(serial, device_path, local_path, device_mtime=device_mtime)

            elif a.kind == "conflict":
                decision = _choose_conflict_policy_per_file(rel, remembered_conflict_policy)

                if decision == "local_apply_all":
                    remembered_conflict_policy = "local"
                    decision = "local"
                elif decision == "device_apply_all":
                    remembered_conflict_policy = "device"
                    decision = "device"
                elif decision == "skip_apply_all":
                    remembered_conflict_policy = "skip"
                    decision = "skip"

                if decision == "local":
                    if not local_path.exists():
                        print(f"[충돌-건너뜀] 로컬 파일 없음: {rel}")
                        continue
                    print(f"[충돌->로컬] 보내기 {rel}")
                    _adb_push(serial, local_path, device_path)
                elif decision == "device":
                    print(f"[충돌->기기] 가져오기 {rel}")
                    device_mtime = dev_map.get(rel, (None, None))[0]
                    _adb_pull(serial, device_path, local_path, device_mtime=device_mtime)
                else:
                    print(f"[충돌-건너뜀] {rel}")

            elif a.kind == "local_deleted":
                print(f"[로컬 삭제됨?] {rel}")
                if _prompt_yes_no("기기에서도 삭제하시겠습니까? (y/N): "):
                    print(f"[기기삭제] {rel}")
                    _adb_delete_device_file(serial, device_path)
                else:
                    print(f"[로컬복구] 가져오기 {rel}")
                    device_mtime = dev_map.get(rel, (None, None))[0]
                    _adb_pull(serial, device_path, local_path, device_mtime=device_mtime)

            elif a.kind == "device_deleted":
                print(f"[기기 삭제됨?] {rel}")
                if _prompt_yes_no("로컬에서도 삭제하시겠습니까? (y/N): "):
                    print(f"[로컬삭제] {rel}")
                    _delete_local_file(local_path)
                else:
                    if not local_path.exists():
                        print(f"[복구-건너뜀] 로컬 파일이 예기치 않게 없습니다: {rel}")
                        continue
                    print(f"[기기복구] 보내기 {rel}")
                    _adb_push(serial, local_path, device_path)

            else:
                print(f"[건너뜀] 알 수 없는 동작: {a.kind} {rel}")

        except Exception as e:
            print(f"[오류] {a.kind.upper()} {rel}: {e}")

    try:
        final_local = _local_list_files_meta(local_dir)
        final_dev = adb_helper.adb_list_files_meta(serial, device_dir)
        SnapshotStore(snapshot_path).save(final_local, final_dev)
    except Exception as e:
        print(f"[경고] 스냅샷 저장 실패: {e}")

    print("\n완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
