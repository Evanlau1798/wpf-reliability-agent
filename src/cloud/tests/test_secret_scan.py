from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).parents[3]
SCANNER = REPO_ROOT / "scripts" / "scan_secrets.py"


def test_secret_scan_rejects_detected_token(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("ghp_" + "A" * 36, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCANNER), str(candidate)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "github-token" in result.stdout
