"""Idempotent seeding of account and channel metadata.

This mirrors the account/channel identifiers configured inside the n8n
workflow (node "04 - Load Account Configuration"). The identifiers must match
exactly, because n8n is the component that resolves them to Buffer profiles.

Buffer profile ids are intentionally NOT seeded here - they stay in n8n.
To add an account: add a row here AND add the matching entry in the n8n config.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Account, Channel

SEED_ACCOUNTS: list[dict] = [
    {
        "id": "company",
        "name": "Company",
        "type": "company",
        "description": "Primary company brand account.",
        "color": "#4F46E5",
        "sort_order": 0,
        "channels": [
            {
                "id": "company_instagram",
                "platform": "instagram",
                "display_name": "Instagram",
                "username": "@fajlefaruk",
                "sort_order": 0,
            },
            {
                "id": "company_facebook",
                "platform": "facebook",
                "display_name": "Facebook",
                "username": "Testing Purpose",
                "sort_order": 1,
            },
            {
                "id": "company_linkedin",
                "platform": "linkedin",
                "display_name": "LinkedIn",
                "username": "tulips-tesha-694136439",
                "sort_order": 2,
            },
        ],
    },
]


def seed(db: Session) -> None:
    """Insert any missing accounts/channels without touching existing rows."""
    existing_accounts = {a.id for a in db.scalars(select(Account)).all()}

    for spec in SEED_ACCOUNTS:
        account = db.get(Account, spec["id"])
        if account is None:
            account = Account(
                id=spec["id"],
                name=spec["name"],
                type=spec["type"],
                description=spec.get("description"),
                logo=spec.get("logo"),
                color=spec.get("color"),
                active=True,
                sort_order=spec.get("sort_order", 0),
            )
            db.add(account)
            existing_accounts.add(spec["id"])
        else:
            # Refresh display metadata but never the user's active flag.
            account.name = spec["name"]
            account.type = spec["type"]
            account.description = spec.get("description")
            account.color = spec.get("color")

        db.flush()

        existing_channels = {c.id for c in db.scalars(select(Channel).where(Channel.account_id == account.id)).all()}
        for ch in spec["channels"]:
            if ch["id"] in existing_channels:
                continue
            db.add(
                Channel(
                    id=ch["id"],
                    account_id=account.id,
                    platform=ch["platform"],
                    display_name=ch["display_name"],
                    username=ch.get("username"),
                    avatar=ch.get("avatar"),
                    description=ch.get("description"),
                    active=True,
                    sort_order=ch.get("sort_order", 0),
                )
            )

    db.commit()