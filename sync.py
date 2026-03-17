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

# adb_helper.py에서 이미 device-side prune(.obsidian/.trash)을 수행하므로,
# 여기서는 local-side만 동일 규칙으로 제외하면 충분합니다.
IGNORE_DIR_NAMES = {".obsidian", ".trash"}

Meta = Tuple[int, int]  # (mtime, size)


# {rel_path: (mtime, size)}
def _local_list_files_meta(local_root: Path) -> Dict[str, Meta]:
    result: Dict[str, Meta] = {}
    for p in local_root.rglob("*"):
        if not p.is_file():
            continue

        rel = p.relative_to(local_root).as_posix()

        # ignore .obsidian / .trash and their descendants
        first = rel.split("/", 1)[0]
        if first in IGNORE_DIR_NAMES:
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
) -> str:
    """
    기본 변경 출력(동일은 출력 안 함):
      - local only:      "{rel} > -Empty-"   (push)
      - device only:     "-Empty- < {rel}"   (pull)
      - both exist:
          - mtime diff:  "{rel} > {rel}" or "{rel} < {rel}"
          - mtime same:
              - size diff: "{rel} ! {rel}"   (conflict)

    삭제 후보는 별도 섹션으로 출력(항상 사용자 확인 필요).
    """
    lines: List[str] = ["Local - Device"]
    all_paths = sorted(set(local_map.keys()) | set(dev_map.keys()))

    deletion_set_local_missing = {d.rel for d in deletions if d.local_missing}
    deletion_set_dev_missing = {d.rel for d in deletions if not d.local_missing}

    for rel in all_paths:
        # 삭제 후보는 아래 섹션에서 따로 안내하므로 여기서는 기본 push/pull 표시를 억제
        if rel in deletion_set_local_missing or rel in deletion_set_dev_missing:
            continue

        in_l = rel in local_map
        in_d = rel in dev_map

        if in_l and not in_d:
            lines.append(f"{rel} > -Empty-")
            continue
        if not in_l and in_d:
            lines.append(f"-Empty- < {rel}")
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
            # else identical -> no output

    if deletions:
        lines.append("")
        lines.append("Deletions (need confirmation)")
        for d in deletions:
            if d.local_missing:
                # 로컬에서 삭제 추정: 디바이스도 삭제? 아니면 복구(pull)?
                lines.append(f"[LOCAL deleted?] {d.rel}  (delete on device? else restore local)")
            else:
                # 디바이스에서 삭제 추정: 로컬도 삭제? 아니면 복구(push)?
                lines.append(f"[DEVICE deleted?] {d.rel}  (delete on local? else restore device)")

    if len(lines) == 1:
        return "Local - Device\n(no changes)\n"
    return "\n".join(lines) + "\n"


def build_sync_plan_once(
    serial: str,
    device_dir: str,
    local_dir: Path,
    snapshot_path: Path,
):
    """
    Returns (plan_text, local_map, dev_map, deletions)
    Note: dev_map already excludes .obsidian/.trash in adb_helper via find -prune.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    local_map = _local_list_files_meta(local_dir)
    dev_map = adb_helper.adb_list_files_meta(serial, device_dir)

    store = SnapshotStore(snapshot_path)
    prev = store.load()
    deletions: List[DeletionCandidate] = []
    if prev is not None:
        deletions = SnapshotStore.compute_deletions(prev, local_map, dev_map)

    plan_text = _format_plan_changed_only(local_map, dev_map, deletions)
    return plan_text, local_map, dev_map, deletions


# ------------------------
# Execution layer (push/pull/delete)
# ------------------------

@dataclass(frozen=True)
class Action:
    kind: str  # "push" | "pull" | "conflict" | "local_deleted" | "device_deleted"
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
        # 삭제 후보는 별도 액션으로
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
            # else identical -> no action

    return actions


def _ensure_device_parent_dir(serial: str, device_path: str) -> None:
    parent = device_path.rsplit("/", 1)[0] if "/" in device_path else device_path
    if not parent:
        return
    # adb shell은 문자열 파싱이라, 단일 문자열 + sh_quote가 가장 안정적
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

    # adb pull often sets mtime to "now" -> fix it using device mtime we already collected
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
        print(f"\n⚠️  Conflict: {rel}")
        print("Choose which version to keep:")
        print("  L) keep LOCAL  (push local -> device)")
        print("  D) keep DEVICE (pull device -> local)")
        print("  S) skip (do nothing)")
        print("  A) apply choice to ALL remaining conflicts (you will be asked once)")
        choice = input("Select [L/D/S/A]: ").strip().lower()

        if choice in ("l", "local"):
            return "local"
        if choice in ("d", "device"):
            return "device"
        if choice in ("s", "skip"):
            return "skip"
        if choice == "a":
            while True:
                g = input("Apply which policy to ALL conflicts? [L/D/S]: ").strip().lower()
                if g in ("l", "local"):
                    return "local_apply_all"
                if g in ("d", "device"):
                    return "device_apply_all"
                if g in ("s", "skip"):
                    return "skip_apply_all"
                print("Invalid. Please choose L/D/S.")
        print("Invalid. Please choose L/D/S/A.")


def _device_path_join(root: str, rel: str) -> str:
    root = root.rstrip("/")
    rel = rel.lstrip("/")
    return f"{root}/{rel}" if rel else root


def main(store: Optional[ConfigStore] = None) -> int:
    if store is None:
        store = ConfigStore.default()
        
    sync_cfg = store.get_sync_config()

    if sync_cfg is None:
        print("Sync config is not initialized. Run device/folder selection first.")
        return 2

    serial = sync_cfg.device.serial
    device_dir = sync_cfg.device_sync_dir
    local_dir = Path(sync_cfg.local_sync_dir)

    if not adb_helper.adb_available():
        print("adb not available on PATH.")
        return 2

    serials = adb_helper.list_connected_serials()
    if serial not in serials:
        print(f"Device not connected/authorized: {serial}")
        print(f"Connected: {serials}")
        return 3

    # snapshot path: config 파일과 같은 폴더에 저장
    snapshot_path = store.path.parent / ".xync_snapshot.json"

    print(f"Device: {serial} ({sync_cfg.device.model})")
    print(f"Device dir: {device_dir}")
    print(f"Local  dir: {local_dir}")
    print(f"Snapshot : {snapshot_path}\n")

    plan_text, local_map, dev_map, deletions = build_sync_plan_once(
        serial, device_dir, local_dir, snapshot_path
    )

    print(plan_text)
    print(f"Local files : {len(local_map)}")
    print(f"Device files: {len(dev_map)}")
    print(f"Deletions   : {len(deletions)}")

    actions = _compute_actions(local_map, dev_map, deletions)
    if not actions:
        print("\nNo changes.")

        # 스냅샷만 갱신(최초 실행 시 삭제 추정 방지 목적)
        SnapshotStore(snapshot_path).save(local_map, dev_map)
        return 0

    pushes = sum(1 for a in actions if a.kind == "push")
    pulls = sum(1 for a in actions if a.kind == "pull")
    conflicts = sum(1 for a in actions if a.kind == "conflict")
    del_local = sum(1 for a in actions if a.kind == "device_deleted")  # device에서 삭제 추정 -> 로컬 삭제 여부 질문
    del_dev = sum(1 for a in actions if a.kind == "local_deleted")     # local에서 삭제 추정 -> 디바이스 삭제 여부 질문

    print("\nSummary:")
    print(f"  push           : {pushes}")
    print(f"  pull           : {pulls}")
    print(f"  conflict       : {conflicts}")
    print(f"  local deleted? : {del_dev}  (ask: delete on device or restore local)")
    print(f"  device deleted?: {del_local} (ask: delete on local or restore device)")

    if not _prompt_yes_no("\nProceed with sync (push/pull/delete)? (y/N): "):
        print("Aborted.")
        return 0

    remembered_conflict_policy: Optional[str] = None

    for a in actions:
        rel = a.rel
        device_path = _device_path_join(device_dir, rel)
        local_path = local_dir / Path(rel)

        try:
            if a.kind == "push":
                if not local_path.exists():
                    print(f"[SKIP] local missing: {rel}")
                    continue
                print(f"[PUSH] {rel}")
                _adb_push(serial, local_path, device_path)

            elif a.kind == "pull":
                print(f"[PULL] {rel}")
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
                        print(f"[SKIP-CONFLICT] local missing: {rel}")
                        continue
                    print(f"[CONFLICT->LOCAL] push {rel}")
                    _adb_push(serial, local_path, device_path)
                elif decision == "device":
                    print(f"[CONFLICT->DEVICE] pull {rel}")
                    device_mtime = dev_map.get(rel, (None, None))[0]
                    _adb_pull(serial, device_path, local_path, device_mtime=device_mtime)
                else:
                    print(f"[CONFLICT-SKIP] {rel}")

            elif a.kind == "local_deleted":
                # 로컬이 사라졌고(삭제 추정), 디바이스에는 남아있음
                print(f"[LOCAL deleted?] {rel}")
                if _prompt_yes_no("Delete on DEVICE as well? (y/N): "):
                    print(f"[DELETE-DEVICE] {rel}")
                    _adb_delete_device_file(serial, device_path)
                else:
                    # 복구(pull)
                    print(f"[RESTORE-LOCAL] pull {rel}")
                    device_mtime = dev_map.get(rel, (None, None))[0]
                    _adb_pull(serial, device_path, local_path, device_mtime=device_mtime)

            elif a.kind == "device_deleted":
                # 디바이스가 사라졌고(삭제 추정), 로컬에는 남아있음
                print(f"[DEVICE deleted?] {rel}")
                if _prompt_yes_no("Delete on LOCAL as well? (y/N): "):
                    print(f"[DELETE-LOCAL] {rel}")
                    _delete_local_file(local_path)
                else:
                    # 복구(push)
                    if not local_path.exists():
                        print(f"[SKIP-RESTORE] local missing unexpectedly: {rel}")
                        continue
                    print(f"[RESTORE-DEVICE] push {rel}")
                    _adb_push(serial, local_path, device_path)

            else:
                print(f"[SKIP] unknown action: {a.kind} {rel}")

        except Exception as e:
            print(f"[ERROR] {a.kind.upper()} {rel}: {e}")

    # 최종 상태 스냅샷 갱신(정확도 위해 재스캔)
    try:
        final_local = _local_list_files_meta(local_dir)
        final_dev = adb_helper.adb_list_files_meta(serial, device_dir)
        SnapshotStore(snapshot_path).save(final_local, final_dev)
    except Exception as e:
        print(f"[WARN] snapshot save failed: {e}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())