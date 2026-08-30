# scripts/database_diagnostics.py — replace with this version
import asyncio, os, sys

async def scan():
    """Generated function header.

    Function: scan
    Path: backend/scripts/migrations/database_diagnostics.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import motor.motor_asyncio
    mongo_url = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGODB_DB") or os.environ.get("DB_NAME", "strataos_production")
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    # Try all known field aliases for the building partition key
    filters = [
        {"building_id": "13195"},
        {"plan_id": "13195"},
        {"building_id": 13195},   # stored as int in some collections
        {"plan_id": 13195},
    ]

    # 1. List all collection names in the DB
    all_cols = await db.list_collection_names()
    print(f"Total collections in {db_name}: {len(all_cols)}")
    print("All collections:", sorted(all_cols))
    print()

    # 2. Check payment-related candidates with all filter variants
    payment_keywords = ["payment", "receipt", "transaction", "levy", "financial", "bank", "deft", "strata_web"]
    candidates = [c for c in all_cols if any(k in c.lower() for k in payment_keywords)]
    print(f"Payment-related collections ({len(candidates)}): {candidates}\n")

    for col in candidates:
        for filt in filters:
            count = await db[col].count_documents(filt)
            if count > 0:
                sample = await db[col].find_one(filt, sort=[("_id", 1)])
                date_val = None
                for f in ["payment_date", "date", "transaction_date", "paid_at", "created_at"]:
                    if sample and sample.get(f):
                        date_val = f"{f}={sample[f]}"
                        break
                print(f"  {col} | filter={list(filt.keys())[0]} | count={count} | {date_val}")
                print(f"    sample keys: {sorted(k for k in sample.keys() if not k.startswith('_'))}")
                break  # found a working filter, no need to try others

    client.close()

asyncio.run(scan())
