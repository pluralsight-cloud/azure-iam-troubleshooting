import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient

load_dotenv()

# these could easily be command-line driven
FROM_ROLE_NAME = "Too Many Perms"
TO_ROLE_NAME   = "Just Right Perms"

# different app registration for role changes
credential = ClientSecretCredential(
    tenant_id=os.getenv("TENANT_ID"),
    client_id=os.getenv("ROLE_CHANGES_CLIENT_ID"),
    client_secret=os.getenv("ROLE_CHANGES_CLIENT_SECRET")
)
client = GraphServiceClient(
    credentials=credential,
    scopes=["https://graph.microsoft.com/.default"]
)

STATE_FILE = Path("role_migration_state.json")

async def get_all_role_definitions():
    """All directory role definitions (built-in + custom)."""
    resp = await client.role_management.directory.role_definitions.get()
    return resp.value or []

async def get_all_role_assignments():
    """All directory role assignments (who has which role)."""
    resp = await client.role_management.directory.role_assignments.get()
    return resp.value or []

async def get_role_definition_by_name(name: str):
    """Return the role definition (unified directory role) matching displayName."""
    definitions = await get_all_role_definitions()
    for d in definitions:
        if d.display_name == name:
            return d
    raise ValueError(f"Directory role definition '{name}' not found.")

async def get_role_definition_id_by_name(name: str) -> str:
    role_def = await get_role_definition_by_name(name)
    print(f"Found role definition: '{role_def.display_name}' → Definition ID: {role_def.id}")
    return role_def.id

async def get_role_assignments_for_definition(role_def_id: str):
    """All assignments whose roleDefinitionId == role_def_id."""
    assignments = await get_all_role_assignments()
    return [a for a in assignments if a.role_definition_id == role_def_id]

async def get_users_for_role_definition(role_def_id: str):
    """
    Return user objects that currently have this directory role definition
    (via direct role assignments; ignoring groups-for-now).
    """
    assignments = await get_role_assignments_for_definition(role_def_id)
    user_ids = [a.principal_id for a in assignments]  # principalId is user/group/SP

    users = []
    for uid in user_ids:
        try:
            u = await client.users.by_user_id(uid).get()
            if getattr(u, "user_principal_name", None):
                users.append(u)
        except Exception:
            # principal might be group/SP; ignore for now
            continue

    return users

async def add_user_to_role_definition(role_def_id: str, user_id: str, upn: str):
    """
    Create a role assignment (user → role definition) at tenant scope ('/').
    """
    # add the role for the user

async def remove_user_from_role_definition(role_def_id: str, user_id: str, upn: str):
    """
    Delete the role assignment for this user+roleDef.
    """
    # remove the role for the user

def save_state(direction: Literal["forward", "rollback"], affected_users: list[dict]):
    state = {
        "timestamp": datetime.now().isoformat() + "Z",
        "direction": direction,
        "from_role": FROM_ROLE_NAME if direction == "forward" else TO_ROLE_NAME,
        "to_role": TO_ROLE_NAME   if direction == "forward" else FROM_ROLE_NAME,
        "affected_users": affected_users
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"\nState saved → {STATE_FILE}")


# ---------- Migration logic using unified roles ----------

async def migrate(direction: Literal["forward", "rollback"] = "forward", dry_run: bool = False):
    source_role_name = FROM_ROLE_NAME if direction == "forward" else TO_ROLE_NAME
    target_role_name = TO_ROLE_NAME   if direction == "forward" else FROM_ROLE_NAME

    print(f"\n{'DRY RUN — NO CHANGES WILL BE MADE' if dry_run else 'LIVE RUN'}")
    print(f"Direction: {source_role_name} → {target_role_name}\n")

    # Get role definition IDs
    source_role_def_id = await get_role_definition_id_by_name(source_role_name)
    target_role_def_id = await get_role_definition_id_by_name(target_role_name)

    # Users that currently have the source role (via assignments)
    members = await get_users_for_role_definition(source_role_def_id)
    if not members:
        print(f"No users found with '{source_role_name}'. Nothing to do.")
        return

    print(f"Found {len(members)} user(s) with '{source_role_name}':")
    for u in members:
        print(f"  • {u.display_name} ({u.user_principal_name})")

    if dry_run:
        print("\nDry-run complete. No changes made.")
        return

    print(f"\nStarting migration...")
    affected = []

    for user in members:
        upn = user.user_principal_name or "unknown"
        print(f"\nProcessing: {user.display_name} ({upn})")

        # Add target role first
        print(f"  Adding '{target_role_name}'...", end="")
        await add_user_to_role_definition(target_role_def_id, user.id, upn)

        # Then remove source role
        print(f"  Removing '{source_role_name}'...", end="")
        await remove_user_from_role_definition(source_role_def_id, user.id, upn)

        affected.append({
            "userId": user.id,
            "displayName": user.display_name,
            "userPrincipalName": upn
        })

    save_state(direction, affected)
    print(f"\nMigration complete! {len(affected)} user(s) updated.")

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate directory roles (unified) with rollback support")
    parser.add_argument("--rollback", action="store_true", help="Reverse the last migration")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without changing anything")
    args = parser.parse_args()

    direction = "rollback" if args.rollback else "forward"

    if args.rollback:
        if not STATE_FILE.exists():
            print("No previous migration found. Cannot rollback.")
            return
        print("ROLLBACK MODE ACTIVATED")

    await migrate(direction=direction, dry_run=args.dry_run)

if __name__ == "__main__":
    asyncio.run(main())