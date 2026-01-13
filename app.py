#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List, Dict

from config_store import ConfigStore, SelectedDevice, SyncConfig
import sync  # ✅ 수동 sync 실행용

from adb_helper import (
    adb_available,
    list_connected_serials,
    get_device_model,
    adb_test_dir,
    adb_list_dir_preview,
)


# ------------------------
# Small local-only helpers
# ------------------------

def input_yes_no(prompt: str) -> bool:
    ans = input(prompt).strip().lower()
    return ans in ("y", "yes", "ㅇ", "ㅇㅇ")


def local_dir_preview(local_dir: Path, max_items: int = 30) -> str:
    try:
        items = sorted(local_dir.iterdir(), key=lambda p: p.name.lower())
    except Exception as e:
        return f"(목록 조회 실패: {e})\n"

    lines = []
    for p in items[:max_items]:
        mark = "/" if p.is_dir() else ""
        lines.append(p.name + mark)
    if len(items) > max_items:
        lines.append(f"... ({len(items) - max_items}개 더 있음)")
    return "\n".join(lines) + ("\n" if lines else "")


# ------------------------
# Interactive steps
# ------------------------

def choose_device_interactively(devices: List[Dict[str, str]]) -> Dict[str, str]:
    print("연결된 기기 목록:")
    for i, d in enumerate(devices, start=1):
        print(f"{i})  {d['serial']} ({d['model']})")

    while True:
        choice = input("\n선택할 기기의 번호를 입력하세요: ").strip()
        if not choice.isdigit():
            print("숫자를 입력해주세요.")
            continue

        idx = int(choice)
        if 1 <= idx <= len(devices):
            return devices[idx - 1]

        print(f"1 ~ {len(devices)} 범위의 번호를 입력해주세요.")


def prompt_and_confirm_device_dir(serial: str) -> str:
    while True:
        device_dir = input("\n[디바이스] 동기화 대상 폴더 경로를 입력하세요 (예: /sdcard/SyncRoot): ").strip()
        if not device_dir:
            print("경로가 비었습니다. 다시 입력해주세요.")
            continue

        if not adb_test_dir(serial, device_dir):
            print("해당 경로가 디바이스에 존재하지 않거나 폴더가 아닙니다. 다시 입력해주세요.")
            continue

        print("\n[디바이스] 폴더 내용 미리보기:")
        print(adb_list_dir_preview(serial, device_dir), end="")

        if input_yes_no("이 디바이스 경로가 맞나요? (y/N): "):
            return device_dir
        print("다시 입력을 진행합니다.")


def prompt_and_confirm_local_dir() -> str:
    while True:
        raw = input("\n[로컬] 동기화 대상 폴더 경로를 입력하세요 (예: ./local_sync): ").strip()
        if not raw:
            print("경로가 비었습니다. 다시 입력해주세요.")
            continue

        local_path = Path(os.path.expanduser(raw)).resolve()

        if not local_path.exists():
            print(f"로컬 폴더가 존재하지 않아 생성합니다: {local_path}")
            try:
                local_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"폴더 생성 실패: {e}")
                continue

        if not local_path.is_dir():
            print("해당 경로는 폴더가 아닙니다. 다시 입력해주세요.")
            continue

        print("\n[로컬] 폴더 내용 미리보기:")
        preview = local_dir_preview(local_path)
        print(preview if preview.strip() else "(비어 있음)\n")

        if input_yes_no("이 로컬 경로가 맞나요? (y/N): "):
            return str(local_path)
        print("다시 입력을 진행합니다.")


def ensure_sync_dirs_configured(store: ConfigStore, device: SelectedDevice, force: bool) -> SyncConfig:
    existing = store.get_sync_config()
    if existing and not force and existing.device.serial == device.serial:
        return existing

    device_dir = prompt_and_confirm_device_dir(device.serial)
    local_dir = prompt_and_confirm_local_dir()

    store.set_sync_dirs(device_dir, local_dir)
    return SyncConfig(device=device, device_sync_dir=device_dir, local_sync_dir=local_dir)


# ------------------------
# Main
# ------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="ADB 기기 선택 + (디바이스/로컬 폴더) 설정 후 수동 동기화 실행"
    )

    parser.add_argument(
        "--initialize", "-i",
        action="store_true",
        help="설정파일이 이미 있어도 다시 선택/입력 과정을 수행합니다."
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="설정파일 경로(기본값: 앱 폴더/.adb_device_selection.json)"
    )

    args = parser.parse_args()

    store = ConfigStore.from_cli_path(args.config) if args.config else ConfigStore.default()

    if not adb_available():
        print("adb를 찾을 수 없거나 실행할 수 없습니다. (PATH 확인)")
        return 2

    # 기기 선택(없거나 -i면)
    saved_device = store.get_selected_device()
    device: SelectedDevice

    if saved_device and not args.initialize:
        device = saved_device
        print(f"이미 설정파일에 저장된 기기가 있습니다: {device.serial} ({device.model})")
    else:
        serials = list_connected_serials()
        if not serials:
            print("연결된 ADB 기기가 없습니다. (adb devices 확인)")
            return 3

        devices = [{"serial": s, "model": get_device_model(s)} for s in serials]
        selected = choose_device_interactively(devices)

        device = SelectedDevice(serial=selected["serial"], model=selected["model"])
        store.set_selected_device(device)

        print("\n저장 완료(기기):")
        print(f"- config: {store.path}")
        print(f"- serial: {device.serial}")
        print(f"- model : {device.model}")

    # ✅ 추가 입력 2개 + 검증 + 사용자 확인
    print("\n동기화 경로 설정을 확인합니다.")
    sync_cfg = ensure_sync_dirs_configured(store, device, force=args.initialize)

    print("\n설정 요약:")
    print(f"- Device: {sync_cfg.device.serial} ({sync_cfg.device.model})")
    print(f"- Device dir: {sync_cfg.device_sync_dir}")
    print(f"- Local  dir: {sync_cfg.local_sync_dir}")

    # ✅ 설정이 끝났으면 바로 수동 sync 실행
    print("\n수동 동기화를 실행합니다...\n")
    return sync.main()  # sync.py의 main() 실행 (plan 출력 후 종료)


if __name__ == "__main__":
    raise SystemExit(main())