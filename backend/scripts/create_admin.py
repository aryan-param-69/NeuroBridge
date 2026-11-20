"""Create the first admin user (MongoDB).

Usage (PowerShell):
    $env:NB_ADMIN_PASSWORD = "..."; uv run python -m scripts.create_admin EMAIL "Full Name"
The password is read from the environment, never from the command line.
"""

import os
import subprocess
import sys

if __name__ == "__main__":
    if not os.environ.get("NB_ADMIN_PASSWORD"):
        sys.exit("Set NB_ADMIN_PASSWORD first.")
    if len(sys.argv) != 3:
        sys.exit('Usage: python -m scripts.create_admin EMAIL "Full Name"')
    env = {**os.environ, "NB_USER_PASSWORD": os.environ["NB_ADMIN_PASSWORD"]}
    sys.exit(subprocess.call([sys.executable, "-m", "scripts.create_user", sys.argv[1], sys.argv[2], "admin"], env=env))
