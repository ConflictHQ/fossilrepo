"""Docker container startup script. Runs migrations and starts the dev server."""

import os
import subprocess
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    print("Running migrations...")
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)

    print("Starting development server...")
    subprocess.run([sys.executable, "manage.py", "runserver", "0.0.0.0:8000"], check=False)


if __name__ == "__main__":
    main()
