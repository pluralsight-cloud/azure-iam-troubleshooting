import os
import json
from datetime import datetime
from dotenv import load_dotenv

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

load_dotenv()

credential = ClientSecretCredential(
    tenant_id=os.getenv("TENANT_ID"),
    client_id=os.getenv("CLIENT_ID"),
    client_secret=os.getenv("CLIENT_SECRET")
)

client = GraphServiceClient(credentials=credential, scopes=["https://graph.microsoft.com/.default"])

# Helper: convert any msgraph model to plain dict (works for CA policies, users, etc.)
def model_to_dict(obj) -> dict:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat() + "Z"
    if isinstance(obj, (list, tuple)):
        return [model_to_dict(i) for i in obj]
    if isinstance(obj, dict):
        return {k: model_to_dict(v) for k, v in obj.items()}
    if hasattr(obj, "additional_data"):
        data = {k: model_to_dict(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
        if obj.additional_data:
            data.update(obj.additional_data)
        return data
    if hasattr(obj, "__dict__"):
        return {k: model_to_dict(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)


async def get_conditional_access_policies():
    policies = []
    resp = await client.identity.conditional_access.policies.get()
    while resp:
        policies.extend(resp.value or [])
        if getattr(resp, "odata_next_link", None):
            resp = await client.identity.conditional_access.policies.with_url(resp.odata_next_link).get()
        else:
            break
    return [model_to_dict(p) for p in policies]


async def get_users_and_roles():
    users_with_roles = []

    query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
        select=["id", "displayName", "userPrincipalName", "onPremisesImmutableId"]
    )
    config = RequestConfiguration(query_parameters=query_params)

    users_resp = await client.users.get(request_configuration=config)
    users = users_resp.value or []

    # Build role lookup
    role_lookup = {}
    roles_resp = await client.directory_roles.get()
    for role in (roles_resp.value or []):
        members_resp = await client.directory_roles.by_directory_role_id(role.id).members.get()
        for member in (members_resp.value or []):
            if getattr(member, "odata_type", None) == "#microsoft.graph.user":
                role_lookup.setdefault(member.id, []).append(role.display_name)

    # Combine
    for user in users:
        roles = role_lookup.get(user.id, [])
        users_with_roles.append({
            "id": user.id,
            "displayName": user.display_name or "",
            "userPrincipalName": user.user_principal_name or "",
            "onPremisesImmutableId": getattr(user, "on_premises_immutable_id", None),
            "directoryRoles": roles
        })

    return sorted(users_with_roles, key=lambda x: x["displayName"] or "")

def generate_report(ca_policies, users_with_roles):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%SZ")
    filename = f"EntraID_Report_{timestamp}.json"

    privileged = [u for u in users_with_roles if u["directoryRoles"]]

    report = {
        "generatedAt": datetime.now().isoformat() + "Z",
        "summary": {
            "totalConditionalAccessPolicies": len(ca_policies),
            "totalUsers": len(users_with_roles),
            "totalPrivilegedUsers": len(privileged)
        },
        "conditionalAccessPolicies": ca_policies,
        "usersWithDirectoryRoles": users_with_roles,
        "privilegedUsersOnly": privileged
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport saved → {os.path.abspath(filename)}")
    print(f"   • CA Policies      : {len(ca_policies)}")
    print(f"   • Total Users      : {len(users_with_roles)}")
    print(f"   • Privileged Users : {len(privileged)}")


# === MAIN ===
import asyncio

async def main():
    print("Fetching Conditional Access policies...")
    ca = await get_conditional_access_policies()
    print(f"→ {len(ca)} policies")

    print("Fetching users + roles...")
    users = await get_users_and_roles()
    print(f"→ {len(users)} users")

    generate_report(ca, users)
    print("\nDone!")

if __name__ == "__main__":
    asyncio.run(main())