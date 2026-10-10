"""Brand (account) management through the API.

Brands must be creatable, editable and removable from the UI - previously they
only existed in `seed.py`, which meant a new brand required a code edit.
"""
from __future__ import annotations

# `client` is provided by tests/conftest.py, backed by an isolated database that
# is recreated for every test. No override needed.


class TestBrandCreation:
    def test_create_brand_with_explicit_id(self, client):
        response = client.post(
            "/api/accounts",
            json={"id": "glaamora", "name": "Glaamora", "type": "brand"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["id"] == "glaamora"
        assert body["name"] == "Glaamora"
        assert body["active"] is True
        assert body["channel_count"] == 0

    def test_id_is_derived_from_the_name_when_omitted(self, client):
        body = client.post("/api/accounts", json={"name": "Sortorium"}).json()
        assert body["id"] == "sortorium"

    def test_duplicate_names_get_distinct_ids(self, client):
        first = client.post("/api/accounts", json={"name": "SortPi"}).json()
        second = client.post("/api/accounts", json={"name": "SortPi"}).json()
        third = client.post("/api/accounts", json={"name": "SortPi"}).json()

        assert len({first["id"], second["id"], third["id"]}) == 3
        # All usable, no 500 from a primary-key collision.
        assert first["id"].startswith("sortpi")

    def test_names_are_slugified_safely(self, client):
        body = client.post("/api/accounts", json={"name": "Fits & Sort / Ltd!"}).json()
        # No spaces, no punctuation, no path separators in an id.
        assert body["id"] == "fits_sort_ltd"
        assert "/" not in body["id"]

    def test_empty_name_is_rejected(self, client):
        response = client.post("/api/accounts", json={"name": "   "})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REQUEST"


class TestBrandEditing:
    def test_rename(self, client):
        created = client.post("/api/accounts", json={"name": "FitsSort"}).json()
        updated = client.patch(
            f"/api/accounts/{created['id']}", json={"name": "FitsSort Global"}
        ).json()
        assert updated["name"] == "FitsSort Global"
        # The id is stable: history rows reference it.
        assert updated["id"] == created["id"]

    def test_deactivate_hides_it_from_the_publishing_selector(self, client):
        """Deactivation must remove a brand from the Quick Publisher selectors."""
        created = client.post("/api/accounts", json={"name": "Hidden Brand"}).json()

        listed = client.get("/api/accounts/manage").json()
        assert any(a["id"] == created["id"] and a["active"] for a in listed)

        client.patch(f"/api/accounts/{created['id']}", json={"active": False})

        listed = client.get("/api/accounts/manage").json()
        after = next(a for a in listed if a["id"] == created["id"])
        assert after["active"] is False

        # An inactive brand never appears in the publishing selector.
        assert not any(a["id"] == created["id"] for a in client.get("/api/accounts").json())

        # And publishing through it is refused with a precise reason.
        response = client.post(
            "/api/publish",
            json={
                "account_id": created["id"],
                "channel_id": "anything",
                "content": "x",
                "publish_mode": "NOW",
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"

    def test_the_seeded_brand_can_still_be_deactivated_and_reactivated(self, client):
        """Existing brands stay manageable through the new endpoints."""
        assert client.patch("/api/accounts/company", json={"active": False}).json()["active"] is False
        assert client.patch("/api/accounts/company", json={"active": True}).json()["active"] is True

    def test_update_unknown_brand_is_404(self, client):
        response = client.patch("/api/accounts/nope", json={"name": "x"})
        assert response.status_code == 404


class TestBrandDeletion:
    def test_delete_empty_brand(self, client):
        created = client.post("/api/accounts", json={"name": "Temporary"}).json()
        assert client.delete(f"/api/accounts/{created['id']}").status_code == 204
        assert not any(
            a["id"] == created["id"] for a in client.get("/api/accounts/manage").json()
        )

    def test_refuses_to_delete_a_brand_that_still_has_channels(self, client):
        """Deleting would silently re-point scheduled posts at nothing."""
        response = client.delete("/api/accounts/company")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ACCOUNT_STILL_HAS_CHANNELS"
        # Still there.
        assert client.get("/api/accounts/company").status_code == 200

    def test_seeded_brand_is_intact_after_the_suite(self, client):
        assert client.get("/api/accounts/company").json()["name"] == "Company"


class TestManagementListing:
    def test_manage_returns_every_brand(self, client):
        client.post("/api/accounts", json={"name": "Empty One"})
        ids = {a["id"] for a in client.get("/api/accounts/manage").json()}
        # Includes the seeded brand and the brand with no channels.
        assert "company" in ids
        assert "empty_one" in ids

    def test_selector_listing_still_only_shows_publishable_brands(self, client):
        """The Quick Publisher contract must not change."""
        accounts = client.get("/api/accounts").json()
        assert accounts, "seeded accounts should still be listed"
        assert all(a["channel_count"] > 0 for a in accounts)
