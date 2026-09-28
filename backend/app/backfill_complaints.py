from .db import SessionLocal
from .services.grouping import backfill_unassigned_messages, reconcile_unaccepted_official_numbers


def main() -> None:
    with SessionLocal() as db:
        count = backfill_unassigned_messages(db)
        withdrawn = reconcile_unaccepted_official_numbers(db)
        db.commit()
    print(f"Complaint backfill: assigned={count} withdrawn_numbers={withdrawn}")


if __name__ == "__main__":
    main()
