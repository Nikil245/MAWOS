"""Audited, idempotent repair for legacy OPEN drives with shortlist rows.

This command never runs during application startup or Alembic migration.
It updates only status/updated_at for qualifying drives and adds one audit
event per changed drive in the same transaction.
"""
import argparse
import datetime as dt
import json
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def reconcile(connection):
    """Apply the narrowly scoped repair in the caller's transaction."""
    ids = connection.execute(text('''
        SELECT d.id FROM placement_drives d
        WHERE d.status = 'OPEN'
          AND EXISTS (SELECT 1 FROM placement_shortlists s WHERE s.drive_id = d.id)
        ORDER BY d.id FOR UPDATE
    ''')).scalars().all()
    changed = []
    for drive_id in ids:
        result = connection.execute(text('''
            UPDATE placement_drives SET status='SHORTLIST_GENERATED', updated_at=:now
            WHERE id=:drive_id AND status='OPEN' RETURNING id
        '''), {'drive_id': drive_id, 'now': dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)}).scalar_one_or_none()
        if result is None:
            continue
        changed.append(result)
        connection.execute(text('''
            INSERT INTO workflow_events
                (workflow_id, topic, agent, hop, payload, created_at, elapsed_ms)
            VALUES (:workflow_id, 'placement.status_reconciled', 'maintenance', 0,
                    :payload, :created_at, 0)
        '''), {'workflow_id': str(uuid.uuid4()),
               'payload': json.dumps({'drive_id': result, 'from': 'OPEN', 'to': 'SHORTLIST_GENERATED'}),
               'created_at': dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)})
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--apply', action='store_true', help='commit; otherwise preview and roll back')
    parser.add_argument('--confirm-database', help='required database name when --apply is used')
    args = parser.parse_args()
    url = make_url(args.database_url)
    if url.get_backend_name() != 'postgresql':
        parser.error('PostgreSQL is required')
    if args.apply and args.confirm_database != url.database:
        parser.error('--confirm-database must exactly match the target database name')

    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        current = connection.execute(text('select current_database()')).scalar_one()
        if current != url.database:
            raise RuntimeError('Connected database does not match the URL database')
        changed = reconcile(connection)
        summary = {'database': current, 'mode': 'apply' if args.apply else 'preview',
                   'changed_drive_ids': changed, 'changed_count': len(changed)}
        if args.apply:
            transaction.commit()
        else:
            transaction.rollback()
        print(json.dumps(summary, sort_keys=True))
    engine.dispose()


if __name__ == '__main__':
    main()
