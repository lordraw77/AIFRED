"""Command-line entry point: ``python -m aifred <command>``."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from aifred import __version__
from aifred.config import API_PORT, Settings

logger = logging.getLogger("aifred")


def _serve(settings: Settings) -> int:
    import uvicorn

    from aifred.app import create_app

    try:
        app = create_app(settings)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    from aifred.hardening import harden_process, start_reaper

    harden_process()  # after the credentials are read: same-user children cannot lift them from /proc
    if os.getpid() == 1:
        start_reaper()  # running as the container's init: collect the orphans of terminal commands
    uvicorn.run(app, host=settings.api_host, port=API_PORT, log_config=None)
    return 0


def _reset_password(settings: Settings, from_stdin: bool) -> int:
    from aifred.app import Services

    services = Services.open(settings)
    try:
        if services.store.admin_get() is None:
            print("No admin account exists yet; start the service with the bootstrap variables.", file=sys.stderr)
            return 1
        if from_stdin:
            password = sys.stdin.readline().rstrip("\n")
        else:
            password = getpass.getpass("New admin password: ")
            if getpass.getpass("Repeat it: ") != password:
                print("The passwords do not match.", file=sys.stderr)
                return 1
        try:
            services.auth.set_password(password)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Password changed; every dashboard session has been logged out.")
        return 0
    finally:
        services.close()


def _migrate_secrets(settings: Settings) -> int:
    from aifred.app import Services
    from aifred.vault import VaultUnavailable

    try:
        services = Services.open(settings)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    try:
        try:
            counts = services.vault.migrate()
        except VaultUnavailable as exc:
            print(f"The secret store is unavailable ({exc}); nothing was changed that could not be repeated.", file=sys.stderr)
            return 1
        print(f"Secrets re-encrypted for the '{services.vault.backend}' backend: {counts['migrated']} migrated, "
              f"{counts['unchanged']} already there, {counts['failed']} failed.")
        key_file = settings.data_dir / ".secret.key"
        if services.vault.backend == "openbao" and counts["failed"] == 0 and key_file.exists():
            print(f"You can now delete {key_file}; no master key needs to stay on the volume.")
        return 1 if counts["failed"] else 0
    finally:
        services.close()


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run a command.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="aifred", description="AIFRED personal agent")
    parser.add_argument("--version", action="version", version=f"aifred {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the API, the dashboard and the Telegram poller")
    admin = commands.add_parser("admin", help="admin account maintenance")
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    reset = admin_commands.add_parser("reset-password", help="set a new admin password")
    reset.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    admin_commands.add_parser("migrate-secrets", help="re-encrypt stored secrets for AIFRED_SECRETS_BACKEND")
    args = parser.parse_args(argv)

    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    settings = Settings()
    if args.command == "serve":
        return _serve(settings)
    if args.admin_command == "migrate-secrets":
        return _migrate_secrets(settings)
    return _reset_password(settings, args.password_stdin)


if __name__ == "__main__":
    sys.exit(main())
