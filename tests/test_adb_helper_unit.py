import unittest
import adb_helper

SERIAL = "R34W300A6QX"
PATHS_SHOULD_EXIST = [
    "/sdcard",
    "/sdcard/Documents/Xync",
]


class TestAdbTestDirIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not adb_helper.adb_available():
            raise unittest.SkipTest("adb is not available on PATH.")

        serials = adb_helper.list_connected_serials()
        if SERIAL not in serials:
            raise unittest.SkipTest(
                f"Device not connected/authorized: {SERIAL}\nConnected: {serials}"
            )

    def test_paths_exist(self):
        for p in PATHS_SHOULD_EXIST:
            with self.subTest(path=p):
                ok = adb_helper.adb_test_dir(SERIAL, p)
                if not ok:
                    self.fail(f"Expected directory to exist on device: {p}\n\n{diag}")


    def test_print_preview_outputs(self):
        for path in PATHS_SHOULD_EXIST:
            print("\n" + "=" * 80)
            print(f"[TARGET] serial={SERIAL}, path={path}")
            print("=" * 80)

            # 참고용: test_dir 결과도 같이 출력
            try:
                ok = adb_helper.adb_test_dir(SERIAL, path)
                print(f"[adb_test_dir] => {ok}")
            except Exception as e:
                print(f"[adb_test_dir] EXCEPTION: {e}")

            # 1) adb_list_dir_preview 출력
            try:
                out = adb_helper.adb_list_dir_preview(SERIAL, path, max_lines=60)
                print("\n[adb_list_dir_preview OUTPUT]")
                print(out.rstrip("\n"))
            except Exception as e:
                print(f"\n[adb_list_dir_preview] EXCEPTION: {e}")


if __name__ == "__main__":
    unittest.main(verbosity=2)