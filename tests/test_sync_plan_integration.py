import unittest
from pathlib import Path

import adb_helper
from sync import build_sync_plan_once


SERIAL = "R34W300A6QX"
DEVICE_DIR = "/sdcard/Documents/Xync"

# 로컬 폴더는 테스트 전용으로 repo 아래 고정(원하는 경로로 바꿔도 됨)
LOCAL_DIR = Path(__file__).resolve().parent.parent / "local_sync_test"


class TestBuildSyncPlanIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not adb_helper.adb_available():
            raise unittest.SkipTest("adb not available on PATH.")

        serials = adb_helper.list_connected_serials()
        if SERIAL not in serials:
            raise unittest.SkipTest(
                f"Device not connected/authorized: {SERIAL}\nConnected: {serials}"
            )

        # 디바이스 경로가 디렉토리인지 확인(문제 원인 좁히기)
        if not adb_helper.adb_test_dir(SERIAL, DEVICE_DIR):
            raise unittest.SkipTest(
                f"Device directory does not exist or not accessible: {DEVICE_DIR}"
            )

    def test_print_plan(self):
        plan_text, local_map, dev_map = build_sync_plan_once(
            serial=SERIAL,
            device_dir=DEVICE_DIR,
            local_dir=LOCAL_DIR,
        )

        print("\n" + "=" * 80)
        print(f"SERIAL={SERIAL}")
        print(f"DEVICE_DIR={DEVICE_DIR}")
        print(f"LOCAL_DIR={LOCAL_DIR}")
        print("-" * 80)
        print(f"Local files: {len(local_map)}")
        print(f"Device files: {len(dev_map)}")
        print("-" * 80)
        print(plan_text)
        print("=" * 80 + "\n")

        # 최소한의 sanity check(원하면 제거 가능)
        self.assertIn("Local - Device", plan_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)