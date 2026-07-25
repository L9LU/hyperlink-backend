"""
HyperLink — Firestore Test Account Cleanup Script

USAGE:
  1. Place this file in your hyperlink-backend project (same place your
     Firebase Admin SDK service account key lives).
  2. Update SERVICE_ACCOUNT_PATH below to point to your service account JSON.
  3. Add/edit the TEST_EMAILS list with the exact test account emails to remove.
  4. DRY RUN (default, safe — lists matches, deletes nothing):
        python cleanup_test_accounts.py
  5. Review the printed list carefully.
  6. ACTUAL DELETE (only after reviewing dry-run output):
        python cleanup_test_accounts.py --confirm-delete
"""

import sys
import firebase_admin
from firebase_admin import credentials, firestore

# ---- CONFIG ----------------------------------------------------------------

SERVICE_ACCOUNT_PATH = "firebase_credentials.json"  # matches your project's file name

# Collection(s) that hold user accounts. Add more if patients/doctors are
# split into separate collections (e.g. ["users"] or ["patients", "doctors"]).
COLLECTIONS_TO_CHECK = ["users"]

# Exact emails to remove. Add as many as you like.
TEST_EMAILS = [
    "testdoctor@hyperlink.com",
    "testdoctor2@hyperlink.com",
    "testdoctor3@hyperlink.com",
    "testdoctor4@hyperlink.com",
    "testpatient@hyperlink.com",
    "testpatient2@hyperlink.com",
    "testpatient3@hyperlink.com",
    "testhighrisk@hyperlink.com",
    "testpharmacist@hyperlink.com",
]

# -----------------------------------------------------------------------------

def init_firebase():
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
    return firestore.client()


def find_matches(db):
    matches = []
    for collection_name in COLLECTIONS_TO_CHECK:
        docs = db.collection(collection_name).stream()
        for doc in docs:
            data = doc.to_dict()
            email = (data.get("email") or "").strip().lower()
            if email in [e.lower() for e in TEST_EMAILS]:
                matches.append({
                    "collection": collection_name,
                    "doc_id": doc.id,
                    "email": data.get("email"),
                    "role": data.get("role", "unknown"),
                })
    return matches


def list_all_emails(db):
    print("\nAll accounts currently in Firestore:\n")
    count = 0
    for collection_name in COLLECTIONS_TO_CHECK:
        docs = db.collection(collection_name).stream()
        for doc in docs:
            data = doc.to_dict()
            print(f"  - [{collection_name}] doc_id={doc.id}  email={data.get('email')!r}  role={data.get('role', 'unknown')}")
            count += 1
    if count == 0:
        print("  (no documents found at all in these collections)")
    print(f"\nTotal: {count} account(s).")


def main():
    confirm_delete = "--confirm-delete" in sys.argv
    list_all = "--list-all" in sys.argv

    db = init_firebase()

    if list_all:
        list_all_emails(db)
        return

    matches = find_matches(db)

    if not matches:
        print("No matching test accounts found. Nothing to do.")
        return

    print(f"\nFound {len(matches)} matching account(s):\n")
    for m in matches:
        print(f"  - [{m['collection']}] doc_id={m['doc_id']}  email={m['email']}  role={m['role']}")

    if not confirm_delete:
        print("\nDRY RUN ONLY — nothing was deleted.")
        print("Review the list above. If it's correct, re-run with:")
        print("  python cleanup_test_accounts.py --confirm-delete")
        return

    print("\n--confirm-delete flag detected. Deleting now...")
    for m in matches:
        db.collection(m["collection"]).document(m["doc_id"]).delete()
        print(f"  Deleted [{m['collection']}] {m['doc_id']} ({m['email']})")

    print(f"\nDone. Deleted {len(matches)} account(s).")


if __name__ == "__main__":
    main()