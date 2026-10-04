"""Create (or reset) an admin panel account.

    python -m scripts.create_admin --username admin --name "Main Admin" --role admin
    # asks for the password (or pass --password, e.g. in automation)

    docker compose exec api python -m scripts.create_admin --username admin --name "Main Admin"

Use --reset to set a new password for an existing account (also unlocks it).
"""

import argparse
import getpass
import sys

from sqlalchemy import func, select

from app import admin_schemas as A
from app.database import SessionLocal
from app.errors import ApiError
from app.models import AdminRole, AdminUser
from app.security import hash_password
from app.services.admin_accounts import check_password_policy, create_admin


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--name", default=None, help="Full name (defaults to the username)")
    ap.add_argument("--role", choices=[r.value for r in AdminRole], default="admin")
    ap.add_argument("--password", default=None)
    ap.add_argument("--reset", action="store_true", help="Reset password of an existing admin")
    args = ap.parse_args()

    password = args.password
    if password is None:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords do not match", file=sys.stderr)
            return 1

    with SessionLocal() as db:
        try:
            existing = db.scalar(select(AdminUser).where(
                func.lower(AdminUser.username) == args.username.lower()))
            if args.reset:
                if existing is None:
                    print(f"Admin '{args.username}' not found", file=sys.stderr)
                    return 1
                check_password_policy(password, existing.username)
                existing.password_hash = hash_password(password)
                existing.failed_logins = 0
                existing.locked_until = None
                existing.is_active = True
                existing.token_version += 1
                db.commit()
                print(f"Password reset for '{existing.username}' ({existing.role.value})")
                return 0
            out = create_admin(db, None, A.AdminUserCreateIn(
                username=args.username, full_name=args.name or args.username,
                role=AdminRole(args.role), password=password, must_change_password=False,
            ))
            print(f"Created admin '{out.username}' with role '{out.role.value}'")
            return 0
        except ApiError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
