"""Account (brand) endpoints.

Reads drive every selector in the UI. The write endpoints let a brand be
created, edited or disabled from Settings without touching seed.py or the
n8n workflow.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.deps import ApiKeyDep, DbDep
from app.schemas.account import (
    AccountCreate,
    AccountDetail,
    AccountSummary,
    AccountUpdate,
)
from app.services import account_service

router = APIRouter(tags=["accounts"])


def _detail(account, db, *, include_inactive: bool = False) -> AccountDetail:
    summary = account_service.account_summary(account, db)
    # `Account.channels` is an unfiltered relationship. Returning it verbatim is
    # how a channel an operator had disabled kept reappearing on the brand page
    # with no way to hide it, so the read goes through the one channel query
    # that applies the same rules as every other endpoint.
    channels = account_service.list_all_channels(
        db, account.id, include_inactive=include_inactive
    )
    return AccountDetail(
        **summary,
        description=account.description,
        created_at=account.created_at,
        updated_at=account.updated_at,
        channels=[account_service.channel_summary(c) for c in channels],
    )


@router.get("/accounts", response_model=list[AccountSummary], dependencies=[ApiKeyDep])
def list_accounts(db: DbDep) -> list[AccountSummary]:
    """Active accounts that have at least one active channel."""
    return [AccountSummary(**account_service.account_summary(a, db)) for a in account_service.list_active_accounts(db)]


@router.get("/accounts/manage", response_model=list[AccountSummary], dependencies=[ApiKeyDep])
def list_all_accounts(db: DbDep) -> list[AccountSummary]:
    """Every brand, including empty and inactive ones, for Settings.

    Registered before `/{account_id}` so the literal path wins.
    """
    return [
        AccountSummary(**account_service.account_summary(a, db))
        for a in account_service.list_all_accounts(db)
    ]


@router.post(
    "/accounts",
    response_model=AccountDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[ApiKeyDep],
)
def create_account(payload: AccountCreate, db: DbDep) -> AccountDetail:
    """Create a brand.

    Note this is also how a brand gets its first channel: create the brand, then
    import channels into it from Buffer Integrations.
    """
    account = account_service.create_account(db, payload)
    return _detail(account, db)


@router.get("/accounts/{account_id}", response_model=AccountDetail, dependencies=[ApiKeyDep])
def get_account(
    account_id: str,
    db: DbDep,
    include_inactive: Annotated[
        bool, Query(description="Include inactive or archived channels (admin use)")
    ] = False,
) -> AccountDetail:
    """A single account with its channels. Drives the Accounts page."""
    return _detail(account_service.get_account(db, account_id), db, include_inactive=include_inactive)


@router.patch("/accounts/{account_id}", response_model=AccountDetail, dependencies=[ApiKeyDep])
def update_account(account_id: str, payload: AccountUpdate, db: DbDep) -> AccountDetail:
    """Rename, restyle or (de)activate a brand."""
    account = account_service.update_account(
        db, account_service.get_account(db, account_id), payload
    )
    return _detail(account, db)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[ApiKeyDep],
)
def delete_account(account_id: str, db: DbDep) -> Response:
    """Delete a brand.

    Refused while anything references it. See
    :func:`app.services.account_service.delete_account`: a brand with publishing
    history is never hard-deleted, because that would cascade away the channel
    rows the history points at. Disabling the brand is the safe alternative.
    """
    account_service.delete_account(db, account_service.get_account(db, account_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)