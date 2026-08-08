from pathlib import Path
import re
import subprocess
import sys


PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "service-account": re.compile(r'"type"\s*:\s*"service_account"'),
    "google-api-key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "github-token": re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"),
    "google-oauth-token": re.compile(r"ya29\.[0-9A-Za-z_-]{20,}"),
    "openai-key": re.compile(r"sk-[0-9A-Za-z_-]{20,}"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def scan(path: Path) -> list[str]:
    data = path.read_bytes()
    if b"\0" in data:
        return []
    text = data.decode("utf-8", errors="ignore")
    return [name for name, pattern in PATTERNS.items() if pattern.search(text)]


def main() -> int:
    paths = [Path(value) for value in sys.argv[1:]] or tracked_files()
    findings = [(path, name) for path in paths for name in scan(path)]
    for path, name in findings:
        print(f"{path}: {name}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
