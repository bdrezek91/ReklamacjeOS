from .db import SessionLocal
from .services.grouping import backfill_unassigned_messages


def main() -> None:
    with SessionLocal() as db:
        count = backfill_unassigned_messages(db)
        db.commit()
    print(f"Stage 2 grouping backfill: assigned={count}")


if __name__ == "__main__":
    main()
