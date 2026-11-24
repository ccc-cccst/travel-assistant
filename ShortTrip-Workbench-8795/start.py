"""Install the pinned project dependencies and start the Windows workbench."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--planner-port", type=int, default=8781)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        sys.exit("ShortTrip requires 64-bit Python 3.12 on Windows.")

    root = Path(__file__).resolve().parent
    if not (root / "ShortTrip/artifacts/data/6de12f4/install.json").is_file():
        sys.exit("The travel snapshot is missing. Download and extract the full ZIP from GitHub Releases.")
    planner = root / "combined version_personalized"
    environment = planner / ".venv"
    python = environment / "Scripts/python.exe"
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)

    requirements = planner / "requirements.txt"
    stamp = environment / "shorttrip-requirements.txt"
    if not stamp.is_file() or stamp.read_bytes() != requirements.read_bytes():
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
            check=True,
        )
        stamp.write_bytes(requirements.read_bytes())

    launcher = root / "shorttrip-webui/shorttrip-webui/Start-ShortTrip.ps1"
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher),
        "-Port", str(args.port), "-PlannerPort", str(args.planner_port),
    ]
    if not args.no_browser:
        command.append("-Open")
    subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
