"""One-time: remove duplicate Plaid items created by re-linking an institution
that was already connected (issue #76).

Keeps the original item (and all its history) and deletes the duplicate items
along with their accounts, balances, transactions and budget assignments. Each
duplicate item is removed at Plaid first (/item/remove) so it stops billing.
Net worth snapshots taken while the duplicates existed are corrected by
subtracting the duplicate accounts' balances for each day.

Dry run by default — prints what would change. Pass --apply to commit.

    .venv/bin/python scripts/remove_duplicate_plaid_items.py --keep 2 --remove 5 6
    .venv/bin/python scripts/remove_duplicate_plaid_items.py --keep 2 --remove 5 6 --apply
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bucket(acct) -> str:
    """Net worth bucket for a Plaid account — mirrors _take_net_worth_snapshot."""
    t, s = acct["type"], (acct["subtype"] or "")
    if t == "depository":
        return "liquid"
    if t in ("credit", "loan"):
        return "liabilities"
    raise ValueError(f"Unsupported account type for dedup: {t}/{s} (account {acct['id']})")


def build_plan(conn, keep_item: int, remove_items: list[int]) -> dict:
    """Validate the request and gather everything that would be deleted/corrected."""
    keep = conn.execute("SELECT * FROM plaid_items WHERE id = ?", (keep_item,)).fetchone()
    if not keep:
        raise ValueError(f"Keep item {keep_item} not found")
    if keep_item in remove_items:
        raise ValueError("Keep item cannot also be removed")

    items = []
    for item_id in remove_items:
        item = conn.execute("SELECT * FROM plaid_items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            raise ValueError(f"Item {item_id} not found")
        if item["institution_id"] != keep["institution_id"]:
            raise ValueError(f"Item {item_id} is a different institution than item {keep_item}")
        items.append(item)

    ph = ",".join("?" * len(remove_items))
    accounts = conn.execute(
        f"SELECT * FROM accounts WHERE plaid_item_id IN ({ph}) ORDER BY id", remove_items
    ).fetchall()
    acct_ids = [a["id"] for a in accounts]

    # Every duplicate account must mirror an account on the kept item, so no
    # real account disappears with the duplicates.
    kept_masks = {
        (r["mask"], r["subtype"])
        for r in conn.execute("SELECT mask, subtype FROM accounts WHERE plaid_item_id = ?", (keep_item,))
    }
    for a in accounts:
        if (a["mask"], a["subtype"]) not in kept_masks:
            raise ValueError(f"Account {a['id']} ({a['name']} ...{a['mask']}) has no match on item {keep_item}")

    aph = ",".join("?" * len(acct_ids)) or "NULL"
    txn_ids = [r[0] for r in conn.execute(
        f"SELECT transaction_id FROM transactions WHERE account_id IN ({aph})", acct_ids
    )]
    tph = ",".join("?" * len(txn_ids)) or "NULL"

    # User work on a duplicate would be lost — refuse rather than guess.
    user_work = conn.execute(
        f"SELECT (SELECT COUNT(*) FROM transaction_assignments WHERE transaction_id IN ({tph}) AND status != 'pending_review')"
        f" + (SELECT COUNT(*) FROM transaction_splits WHERE transaction_id IN ({tph}))"
        f" + (SELECT COUNT(*) FROM transaction_metadata WHERE transaction_id IN ({tph}))",
        txn_ids * 3,
    ).fetchone()[0]
    if user_work:
        raise ValueError(f"{user_work} approved assignments/splits/notes exist on duplicate transactions — aborting")

    assignments = conn.execute(
        f"SELECT COUNT(*) FROM transaction_assignments WHERE transaction_id IN ({tph})", txn_ids
    ).fetchone()[0]
    balances = conn.execute(
        f"SELECT COUNT(*) FROM account_balances WHERE account_id IN ({aph})", acct_ids
    ).fetchone()[0]

    # Per-day net worth correction: what the duplicates added to each snapshot.
    first_day = min((a["created_at"][:10] for a in accounts), default=None)
    corrections = []
    if first_day:
        for snap in conn.execute(
            "SELECT snapped_at FROM net_worth_snapshots WHERE snapped_at >= ? ORDER BY snapped_at", (first_day,)
        ).fetchall():
            day = str(snap["snapped_at"])
            delta = {"liquid": 0.0, "liabilities": 0.0}
            for a in accounts:
                row = conn.execute(
                    "SELECT current FROM account_balances WHERE account_id = ? AND snapped_at <= ? "
                    "AND current IS NOT NULL ORDER BY snapped_at DESC LIMIT 1",
                    (a["id"], day),
                ).fetchone()
                if row:
                    delta[_bucket(a)] += row["current"]
            corrections.append({"day": day, **delta})

    return {
        "items": items,
        "accounts": accounts,
        "acct_ids": acct_ids,
        "txn_ids": txn_ids,
        "assignments": assignments,
        "balances": balances,
        "corrections": corrections,
    }


def apply_plan(conn, plan: dict) -> None:
    """Delete the duplicates and correct net worth snapshots in one transaction."""
    acct_ids, txn_ids = plan["acct_ids"], plan["txn_ids"]
    aph = ",".join("?" * len(acct_ids)) or "NULL"
    tph = ",".join("?" * len(txn_ids)) or "NULL"
    conn.execute(f"DELETE FROM transaction_assignments WHERE transaction_id IN ({tph})", txn_ids)
    conn.execute(f"DELETE FROM transactions WHERE account_id IN ({aph})", acct_ids)
    conn.execute(f"DELETE FROM account_balances WHERE account_id IN ({aph})", acct_ids)
    conn.execute(f"DELETE FROM accounts WHERE id IN ({aph})", acct_ids)
    for item in plan["items"]:
        conn.execute("DELETE FROM plaid_items WHERE id = ?", (item["id"],))
    for c in plan["corrections"]:
        conn.execute(
            "UPDATE net_worth_snapshots SET liquid = liquid - ?, liabilities = liabilities - ?, "
            "total = total - ? + ? WHERE snapped_at = ?",
            (c["liquid"], c["liabilities"], c["liquid"], c["liabilities"], c["day"]),
        )


def _remove_at_plaid(item) -> None:
    """Remove the item at Plaid so it stops billing. Already-removed is fine."""
    import json
    import plaid
    from plaid.model.item_remove_request import ItemRemoveRequest
    from api.encryption import decrypt
    from api.plaid_client import get_plaid_client

    try:
        get_plaid_client().item_remove(ItemRemoveRequest(access_token=decrypt(item["access_token_enc"])))
        print(f"  Plaid: removed item {item['id']}")
    except plaid.ApiException as e:
        code = json.loads(e.body).get("error_code")
        if code != "ITEM_NOT_FOUND":
            raise
        print(f"  Plaid: item {item['id']} already removed")


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", type=int, required=True, help="plaid_items.id to keep")
    parser.add_argument("--remove", type=int, nargs="+", required=True, help="plaid_items.id values to remove")
    parser.add_argument("--apply", action="store_true", help="commit changes (default is dry run)")
    parser.add_argument("--db", default=os.path.join("data", "vaultic.db"))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    plan = build_plan(conn, args.keep, args.remove)

    print(f"Keep item {args.keep}; remove items {args.remove}")
    for a in plan["accounts"]:
        print(f"  account {a['id']}: {a['name']} ...{a['mask']} (item {a['plaid_item_id']})")
    print(f"  transactions: {len(plan['txn_ids'])}  assignments: {plan['assignments']}  balances: {plan['balances']}")
    print("  net worth corrections:")
    for c in plan["corrections"]:
        print(f"    {c['day']}: liquid -{c['liquid']:,.2f}  liabilities -{c['liabilities']:,.2f}")

    if not args.apply:
        print("DRY RUN — nothing changed. Re-run with --apply.")
        return

    backup_path = os.path.join(os.path.dirname(args.db), f"vaultic-pre-76-{datetime.now():%Y%m%d%H%M%S}.db")
    with sqlite3.connect(backup_path) as backup:
        conn.backup(backup)
    print(f"Backup written: {backup_path}")

    for item in plan["items"]:
        _remove_at_plaid(item)
    with conn:
        apply_plan(conn, plan)
    print("Applied.")


if __name__ == "__main__":
    main()
