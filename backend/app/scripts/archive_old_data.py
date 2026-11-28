import argparse
import asyncio
import calendar

from ..core.database import Resources
from ..models.mysql_models import now
from ..services.archive_service import archive_batch, prune_messages


async def run(args):
    resources = Resources()
    cutoff = now()
    year = cutoff.year - 1
    cutoff = cutoff.replace(year=year, day=min(cutoff.day, calendar.monthrange(year, cutoff.month)[1]))
    try:
        await resources.initialize_schema()
        personal = group = 0
        for _ in range(args.max_batches):
            a, b = await archive_batch(resources, cutoff, args.batch_size)
            personal += a
            group += b
            if a == b == 0:
                break
            await asyncio.sleep(.1)
        deleted = await prune_messages(resources.mongo, args.retention_days, args.batch_size,
            max_batches=args.max_batches) if args.retention_days else 0
        print(f"Archived: {personal} personal entries, {group} group expenses. Removed {deleted} chat records.")
    finally:
        await resources.close()


def main():
    parser = argparse.ArgumentParser(description="Archive settled financial history. Chat retention is opt-in.")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=10)
    parser.add_argument("--retention-days", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500 or not 1 <= args.max_batches <= 100 or args.retention_days < 0:
        parser.error("Use batch-size 1–500, max-batches 1–100 and retention-days >= 0.")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
