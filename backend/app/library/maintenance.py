"""Run explicitly: python -m backend.app.library.maintenance [--batch-size 200]."""
import argparse
from ..database import SessionLocal
from .service import expire_batch


def main():
    parser = argparse.ArgumentParser(description='Expire library reservations; no scheduler is started.')
    parser.add_argument('--batch-size', type=int, default=200)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000:
        parser.error('--batch-size must be between 1 and 1000')
    with SessionLocal() as db:
        count = expire_batch(db, args.batch_size)
    print(f'Expired {count} library reservations.')


if __name__ == '__main__':
    main()
