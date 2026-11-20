import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def main():
    while True:
        with open(os.path.join(ROOT, "server_watchdog.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " starting server\n")
        try:
            proc = subprocess.Popen(
                [PY, "-m", "server", "--port", "8000"],
                cwd=ROOT,
                stdout=open(os.path.join(ROOT, "server_run5.log"), "a", encoding="utf-8"),
                stderr=open(os.path.join(ROOT, "server_run5_err.log"), "a", encoding="utf-8"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            code = proc.wait()
        except Exception as exc:
            code = 1
        with open(os.path.join(ROOT, "server_watchdog.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" server exited code={code}; restarting in 3s\n")
        time.sleep(3)


if __name__ == "__main__":
    main()
