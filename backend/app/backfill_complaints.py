from .db import SessionLocal
from .services.grouping import backfill_official_numbers, backfill_unassigned_messages


def main() -> None:
    with SessionLocal() as db:
        count = backfill_unassigned_messages(db)
        numbered = backfill_official_numbers(db)
        db.commit()
    print(f"Complaint backfill: assigned={count} numbered={numbered}")


if __name__ == "__main__":
    main()
