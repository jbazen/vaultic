"""Tests for manual entry deduplication (issue #48).

Covers:
  - add_entry Tier 2 (name, category) fallback: same account re-entered with a
    different account_number string updates the existing row + heals the number,
    instead of creating a duplicate.
  - Snapshot history follows the account_number/name correction.
  - Tier 1 exact account_number match still works; genuinely different names
    stay separate; singleton categories unaffected.
  - _migrate_merge_duplicate_manual_entries consolidates existing duplicates
    (newest wins) and is idempotent.
"""
import pytest
from tests.conftest import _test_get_db
import api.database as db_module


@pytest.fixture(autouse=True)
def _cleanup():
    """Remove this module's test rows from the shared in-memory DB."""
    yield
    with _test_get_db() as conn:
        conn.execute("DELETE FROM manual_entry_snapshots WHERE name LIKE 'ZZTEST_%'")
        conn.execute("DELETE FROM manual_entries WHERE name LIKE 'ZZTEST_%'")
        conn.commit()


class TestAddEntryDeduplication:
    def test_changed_account_number_updates_not_duplicates(self, client, auth_headers):
        """The HSA case: 'OPTUM' placeholder in May, real number in June."""
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_HSA", "category": "liquid", "value": 1158.03,
            "account_number": "OPTUM", "entered_at": "2026-05-02",
        })
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_HSA", "category": "liquid", "value": 1544.06,
            "account_number": "430941440", "entered_at": "2026-06-01",
        })
        with _test_get_db() as conn:
            rows = conn.execute(
                "SELECT value, account_number FROM manual_entries WHERE name = 'ZZTEST_HSA'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 1544.06
        assert rows[0]["account_number"] == "430941440"  # healed to the new number

    def test_changed_account_number_preserves_history(self, client, auth_headers):
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_HSA", "category": "liquid", "value": 1158.03,
            "account_number": "OPTUM", "entered_at": "2026-05-02",
        })
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_HSA", "category": "liquid", "value": 1544.06,
            "account_number": "430941440", "entered_at": "2026-06-01",
        })
        with _test_get_db() as conn:
            entry_id = conn.execute(
                "SELECT id FROM manual_entries WHERE name = 'ZZTEST_HSA'"
            ).fetchone()["id"]
        hist = client.get(f"/api/manual/{entry_id}/history", headers=auth_headers).json()
        by_date = {h["snapped_at"]: h["current"] for h in hist}
        assert by_date.get("2026-05-02") == 1158.03  # May point re-keyed and kept
        assert by_date.get("2026-06-01") == 1544.06

    def test_leading_zero_account_number_updates_not_duplicates(self, client, auth_headers):
        """The Rocket Mortgage case: '0704931286' vs '704931286'."""
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_MORTGAGE", "category": "other_liability", "value": 185844.72,
            "account_number": "0704931286", "entered_at": "2026-05-01",
        })
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_MORTGAGE", "category": "other_liability", "value": 184564.93,
            "account_number": "704931286", "entered_at": "2026-06-01",
        })
        with _test_get_db() as conn:
            rows = conn.execute(
                "SELECT value, account_number FROM manual_entries WHERE name = 'ZZTEST_MORTGAGE'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["account_number"] == "704931286"

    def test_exact_account_number_still_matches(self, client, auth_headers):
        for val in (100.0, 250.0):
            client.post("/api/manual", headers=auth_headers, json={
                "name": "ZZTEST_SAME", "category": "liquid", "value": val,
                "account_number": "ACCT123", "entered_at": "2026-06-01",
            })
        with _test_get_db() as conn:
            rows = conn.execute(
                "SELECT value FROM manual_entries WHERE name = 'ZZTEST_SAME'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 250.0

    def test_different_names_stay_separate(self, client, auth_headers):
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_ACCT_A", "category": "liquid", "value": 10.0,
            "account_number": "AAA", "entered_at": "2026-06-01",
        })
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_ACCT_B", "category": "liquid", "value": 20.0,
            "account_number": "BBB", "entered_at": "2026-06-01",
        })
        with _test_get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM manual_entries WHERE name LIKE 'ZZTEST_ACCT_%'"
            ).fetchone()[0]
        assert n == 2  # different names must not merge on Tier 2

    def test_ambiguous_name_does_not_merge(self, client, auth_headers):
        """Two existing same-name rows (no acct) — a new acct must not pick one."""
        with _test_get_db() as conn:
            for i in range(2):
                conn.execute(
                    "INSERT INTO manual_entries (name, category, value, entered_at) VALUES (?,?,?,?)",
                    ("ZZTEST_DUP", "liquid", float(i), "2026-05-01"),
                )
            conn.commit()
        client.post("/api/manual", headers=auth_headers, json={
            "name": "ZZTEST_DUP", "category": "liquid", "value": 99.0,
            "account_number": "NEWACCT", "entered_at": "2026-06-01",
        })
        with _test_get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM manual_entries WHERE name = 'ZZTEST_DUP'"
            ).fetchone()[0]
        # Two pre-existing + one new (fallback skipped because ambiguous) = 3
        assert n == 3


class TestMergeDuplicateMigration:
    def _seed_duplicate(self, conn):
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, account_number, entered_at) "
            "VALUES (?,?,?,?,?)", ("ZZTEST_HSA", "liquid", 1158.03, "OPTUM", "2026-05-02"))
        conn.execute(
            "INSERT INTO manual_entries (name, category, value, account_number, entered_at) "
            "VALUES (?,?,?,?,?)", ("ZZTEST_HSA", "liquid", 1544.06, "430941440", "2026-06-01"))
        conn.execute(
            "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
            "VALUES (?,?,?,?,?)", ("ZZTEST_HSA", "OPTUM", "liquid", 1158.03, "2026-05-02"))
        conn.execute(
            "INSERT INTO manual_entry_snapshots (name, account_number, category, value, snapped_at) "
            "VALUES (?,?,?,?,?)", ("ZZTEST_HSA", "430941440", "liquid", 1544.06, "2026-06-01"))
        conn.commit()

    def test_merges_to_canonical_newest_wins(self):
        with _test_get_db() as conn:
            self._seed_duplicate(conn)
            db_module._migrate_merge_duplicate_manual_entries(conn)
            rows = conn.execute(
                "SELECT value, account_number FROM manual_entries WHERE name = 'ZZTEST_HSA'"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["value"] == 1544.06
            assert rows[0]["account_number"] == "430941440"

    def test_snapshots_rekeyed_onto_canonical(self):
        with _test_get_db() as conn:
            self._seed_duplicate(conn)
            db_module._migrate_merge_duplicate_manual_entries(conn)
            snaps = conn.execute(
                "SELECT snapped_at, account_number FROM manual_entry_snapshots "
                "WHERE name = 'ZZTEST_HSA' ORDER BY snapped_at"
            ).fetchall()
            assert len(snaps) == 2  # both months survive
            assert all(s["account_number"] == "430941440" for s in snaps)

    def test_idempotent(self):
        with _test_get_db() as conn:
            self._seed_duplicate(conn)
            db_module._migrate_merge_duplicate_manual_entries(conn)
            db_module._migrate_merge_duplicate_manual_entries(conn)  # second run = no-op
            n = conn.execute(
                "SELECT COUNT(*) FROM manual_entries WHERE name = 'ZZTEST_HSA'"
            ).fetchone()[0]
            assert n == 1
