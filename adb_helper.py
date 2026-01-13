# adb_helper.py

from __future__ import annotations

import subprocess
from typing import Dict, List, Tuple


def run(cmd: List[str], timeout: int = 10) -> str:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"Command failed ({p.returncode}): {' '.join(cmd)}\n"
            f"stderr: {p.stderr.strip()}"
        )
    return p.stdout


def sh_quote(s: str) -> str:
    # POSIX sh 안전 quoting
    return "'" + s.replace("'", "'\"'\"'") + "'"


def adb_shell_sh_c(serial: str, script: str, timeout: int = 15) -> str:
    """
    항상 'sh -c'로 실행해서 quoting/redirect를 안정적으로 처리.
    """
    return run(["adb", "-s", serial, "shell", "sh", "-c", script], timeout=timeout)


def adb_available() -> bool:
    try:
        run(["adb", "version"], timeout=5)
        return True
    except Exception:
        return False


def list_connected_serials() -> List[str]:
    out = run(["adb", "devices"], timeout=10)
    serials: List[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices attached"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def get_device_model(serial: str) -> str:
    try:
        model = run(
            ["adb", "-s", serial, "shell", "getprop", "ro.product.model"],
            timeout=10,
        ).strip()
        return model if model else "Unknown"
    except Exception:
        return "Unknown"


def adb_test_dir(serial: str, device_path: str) -> bool:
    """
    More robust: avoid 'sh -c' and check via 'ls -ld <path>'.
    """
    try:
        # If it's a directory (or symlink-to-dir), ls -ld should succeed.
        run(["adb", "-s", serial, "shell", "ls", "-ld", device_path], timeout=10)
        return True
    except Exception:
        return False


def adb_list_dir_preview(serial: str, device_path: str, max_lines: int = 40) -> str:
    """
    Robust preview: run 'ls -la <path>' with argv form (no sh -c).
    """
    out = run(["adb", "-s", serial, "shell", "ls", "-la", device_path], timeout=20)
    lines = out.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... (truncated, total {len(out.splitlines())} lines)"]
    return "\n".join(lines) + "\n"


# {rel_path: (mtime_epoch_sec, size_bytes)}
def adb_list_files_meta(serial: str, device_root_dir: str, timeout: int = 45) -> Dict[str, Tuple[int, int]]:
    """
    Return {relative_path: (mtime_epoch_sec, size_bytes)} for all files under device_root_dir.

    NOTE:
    - Excludes ./ .obsidian and ./ .trash (and their descendants) at the LISTING stage.
    """
    root_q = sh_quote(device_root_dir.rstrip("/"))

    # Exclude .obsidian/.trash using find -prune
    prune_expr = (
        r"\( -path './.obsidian' -o -path './.obsidian/*' -o -path './.trash' -o -path './.trash/*' \) -prune -o "
    )

    # 공백 없는 delimiter '|' 사용: mtime|size|name
    cmd_str = (
        f"cd {root_q} 2>/dev/null && "
        f"(find . {prune_expr}"
        f"-type f -exec stat -c '%Y|%s|%n' {{}} + 2>/dev/null "
        f"|| "
        f"find . {prune_expr}"
        f"-type f -exec toybox stat -c '%Y|%s|%n' {{}} + 2>/dev/null)"
    )

    out = run(["adb", "-s", serial, "shell", cmd_str], timeout=timeout)

    result: Dict[str, Tuple[int, int]] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue

        # "<mtime>|<size>|./path"
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue

        try:
            mtime = int(parts[0])
            size = int(parts[1])
        except ValueError:
            continue

        path = parts[2].strip()
        if path.startswith("./"):
            path = path[2:]
        if path:
            result[path] = (mtime, size)

    return result