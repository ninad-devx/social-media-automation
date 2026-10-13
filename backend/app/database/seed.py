"""First-run seeding of demo accounts and channels.

Two hard rules, both learned the hard way:

1. **Never resurrect anything.** The previous implementation ran on *every*
   application boot and re-inserted its hardcoded channels whenever those ids
   were absent, so a channel an administrator had deliberately removed came
   back on the next restart or redeploy. Seeding now happens only on a database
   that has no accounts at all - a genuine first run - and never afterwards.

2. **Never overwrite operator edits.** The old code assigned
   ``account.name``, ``account.type``, ``account.description`` and
   ``account.color`` on every boot, silently reverting anything a human had
   changed in Settings.

This module is development/fixture data only. It is not a production
initialisation path and is never used to create real company channels.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import Account, Channel

logger = logging.getLogger("app.database.seed")

#: Demo data for a brand new local install. Not production content.
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


def database_is_empty(db: Session) -> bool:
    """True only when no account row exists at all."""
    return int(db.scalar(select(func.count()).select_from(Account)) or 0) == 0


def seed(db: Session) -> int:
    """Create the demo brand, and only on a genuinely empty database.

    Returns the number of rows inserted so the caller can log it. Safe to call
    on every boot: after the first run it is a single cheap count.
    """
    if not database_is_empty(db):
        logger.info("db.seed_skipped: the database already has accounts")
        return 0

    logger.warning(
        "db.seed_demo_data: inserting the demo 'company' brand into an empty "
        "database. This is development fixture data."
    )

    for spec in SEED_ACCOUNTS:
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
        db.flush()

        for ch in spec["channels"]:
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
    return 1 + sum(len(spec["channels"]) for spec in SEED_ACCOUNTS)