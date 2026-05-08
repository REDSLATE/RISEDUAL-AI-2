#!/usr/bin/env python3
"""RISEDUAL AI — Database Migration Tool

Exports all data from the current MongoDB and imports it into any target database.
No API needed — direct database-to-database pipeline.

Usage:
  # Export current data to a JSON dump file
  python3 migrate_db.py export

  # Import into a production MongoDB (mongodb+srv:// or any connection string)
  python3 migrate_db.py import "mongodb+srv://user:pass@cluster.mongodb.net" "risedual"

  # Direct transfer: copy everything from current DB to a target DB
  python3 migrate_db.py transfer "mongodb+srv://user:pass@cluster.mongodb.net" "risedual"
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId, json_util
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).parent / "backend" / ".env")

DUMP_DIR = Path("/app/db_dumps")

# Collections to always migrate (core data)
PRIORITY_COLLECTIONS = [
    "users", "features_snapshots", "paper_trades", "paper_portfolios",
    "paper_positions", "predictions", "strategies", "marketplace_strategies",
    "watchlists", "api_keys", "api_key_vault", "credit_events",
    "user_credits", "chat_sessions", "chat_memories",
    "fear_greed_index", "lobbying_data", "headlines",
    "market_memory_log", "sentiment_history", "notifications",
]


def _serialize(doc):
    """Convert MongoDB doc to JSON-safe dict."""
    return json.loads(json_util.dumps(doc))


def _deserialize(doc):
    """Convert JSON-safe dict back to MongoDB-native types."""
    return json_util.loads(json.dumps(doc))


async def export_data():
    """Export all collections to JSON files in /app/db_dumps/."""
    source_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "risedual")

    client = AsyncIOMotorClient(source_url)
    db = client[db_name]

    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dump_path = DUMP_DIR / timestamp
    dump_path.mkdir(parents=True, exist_ok=True)

    collections = await db.list_collection_names()
    manifest = {"exported_at": datetime.now(timezone.utc).isoformat(), "db": db_name, "collections": {}}

    for coll_name in sorted(collections):
        count = await db[coll_name].count_documents({})
        if count == 0:
            continue

        print(f"  Exporting {coll_name} ({count:,} docs)...", end=" ", flush=True)
        docs = []
        async for doc in db[coll_name].find({}):
            docs.append(_serialize(doc))

        out_file = dump_path / f"{coll_name}.json"
        with open(out_file, "w") as f:
            json.dump(docs, f, default=str)

        manifest["collections"][coll_name] = count
        print(f"OK")

    # Save manifest
    with open(dump_path / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    total = sum(manifest["collections"].values())
    print(f"\n  Export complete: {len(manifest['collections'])} collections, {total:,} documents")
    print(f"  Saved to: {dump_path}")

    client.close()
    return str(dump_path)


async def import_data(target_url: str, target_db_name: str, dump_path: str = None):
    """Import JSON dump files into a target MongoDB."""
    if not dump_path:
        # Use latest dump
        dumps = sorted(DUMP_DIR.iterdir()) if DUMP_DIR.exists() else []
        if not dumps:
            print("  No dumps found. Run 'export' first.")
            return
        dump_path = str(dumps[-1])

    dump_dir = Path(dump_path)
    if not dump_dir.exists():
        print(f"  Dump directory not found: {dump_path}")
        return

    client = AsyncIOMotorClient(target_url)
    db = client[target_db_name]

    # Test connection
    try:
        await db.command("ping")
        print(f"  Connected to target: {target_db_name}")
    except Exception as e:
        print(f"  Failed to connect: {e}")
        client.close()
        return

    json_files = sorted(dump_dir.glob("*.json"))
    json_files = [f for f in json_files if f.name != "manifest.json"]

    for jf in json_files:
        coll_name = jf.stem
        with open(jf, "r") as f:
            docs = json.load(f)

        if not docs:
            continue

        print(f"  Importing {coll_name} ({len(docs):,} docs)...", end=" ", flush=True)

        # Deserialize back to native BSON types
        native_docs = [_deserialize(d) for d in docs]

        # Drop existing and insert fresh
        await db[coll_name].drop()
        batch_size = 1000
        for i in range(0, len(native_docs), batch_size):
            batch = native_docs[i:i + batch_size]
            await db[coll_name].insert_many(batch)

        print("OK")

    total = sum(len(json.load(open(f))) for f in json_files)
    print(f"\n  Import complete: {len(json_files)} collections, {total:,} documents")
    print(f"  Target: {target_db_name}")

    client.close()


async def transfer_direct(target_url: str, target_db_name: str):
    """Direct database-to-database transfer (no files on disk)."""
    source_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    source_db_name = os.environ.get("DB_NAME", "risedual")

    source_client = AsyncIOMotorClient(source_url)
    target_client = AsyncIOMotorClient(target_url)

    source_db = source_client[source_db_name]
    target_db = target_client[target_db_name]

    # Test target connection
    try:
        await target_db.command("ping")
        print(f"  Connected to target: {target_db_name}")
    except Exception as e:
        print(f"  Failed to connect to target: {e}")
        source_client.close()
        target_client.close()
        return

    collections = await source_db.list_collection_names()
    total_docs = 0

    for coll_name in sorted(collections):
        count = await source_db[coll_name].count_documents({})
        if count == 0:
            continue

        print(f"  Transferring {coll_name} ({count:,} docs)...", end=" ", flush=True)

        await target_db[coll_name].drop()

        batch = []
        async for doc in source_db[coll_name].find({}):
            batch.append(doc)
            if len(batch) >= 1000:
                await target_db[coll_name].insert_many(batch)
                batch = []

        if batch:
            await target_db[coll_name].insert_many(batch)

        total_docs += count
        print("OK")

    print(f"\n  Transfer complete: {len(collections)} collections, {total_docs:,} documents")
    print(f"  Source: {source_db_name} → Target: {target_db_name}")

    source_client.close()
    target_client.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()

    if command == "export":
        print("\n[EXPORT] Dumping current database to files...")
        asyncio.run(export_data())

    elif command == "import":
        if len(sys.argv) < 4:
            print('Usage: python3 migrate_db.py import "mongodb+srv://..." "db_name" [dump_path]')
            return
        target_url = sys.argv[2]
        target_db = sys.argv[3]
        dump_path = sys.argv[4] if len(sys.argv) > 4 else None
        print(f"\n[IMPORT] Loading data into {target_db}...")
        asyncio.run(import_data(target_url, target_db, dump_path))

    elif command == "transfer":
        if len(sys.argv) < 4:
            print('Usage: python3 migrate_db.py transfer "mongodb+srv://..." "db_name"')
            return
        target_url = sys.argv[2]
        target_db = sys.argv[3]
        print(f"\n[TRANSFER] Direct pipeline → {target_db}...")
        asyncio.run(transfer_direct(target_url, target_db))

    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
