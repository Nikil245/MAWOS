"""Placement details, URL and private PDF security contracts."""
import datetime as dt
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.placement import api
from backend.app.placement.schemas import DriveInput
from backend.app.placement.service import PlacementError, PlacementService
from backend.app.placement.storage import LocalPlacementDocumentStorage
from test_placement import drive_input, placement_client, placement_db  # noqa: F401


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'data:text/html,bad', 'file:///etc/passwd',
    'https://user:secret@example.com/jobs', 'http://localhost/jobs',
    'http://127.0.0.1/jobs', 'https://10.0.0.1/jobs', 'https://example.com/white space',
    'not a url',
])
def test_application_url_rejects_unsafe_values(url):
    with pytest.raises(ValidationError):
        drive_input(application_url=url)


def test_description_and_application_url_normalization():
    data = drive_input(description='  First line\r\nSecond line  ',
                       application_url=' HTTPS://Example.COM/jobs?id=4 ')
    assert data.description == 'First line\nSecond line'
    assert data.application_url == 'https://example.com/jobs?id=4'


def test_apply_action_state_rules(placement_db):
    db, _ = placement_db
    service = PlacementService()
    drive_id = service.save_drive(db, drive_input(
        application_url='https://careers.example.com/jobs/1',
        application_deadline=dt.date.today()))['id']
    service.generate(db, drive_id)
    assert service.student_detail(db, drive_id, 'P4')['can_apply'] is True
    service.transition(db, drive_id, 'close')
    detail = service.student_detail(db, drive_id, 'P4')
    assert detail['can_apply'] is False and 'closed' in detail['apply_message']


def test_draft_is_not_student_visible(placement_db):
    db, _ = placement_db
    service = PlacementService()
    drive_id = service.save_drive(db, drive_input(status='DRAFT'))['id']
    assert service.list_drives(db, admin=False, usn='P4') == []
    with pytest.raises(PlacementError) as exc:
        service.student_detail(db, drive_id, 'P4')
    assert exc.value.status == 404


def test_valid_pdf_upload_download_replace_and_remove(placement_client, monkeypatch, tmp_path):
    client, user, drive_id, _ = placement_client
    user.id = 7
    storage = LocalPlacementDocumentStorage(tmp_path, 1024)
    monkeypatch.setattr(api, 'placement_document_storage', lambda: storage)
    root = f'/api/placements/drives/{drive_id}/document'

    response = client.post(root, files={'document': ('job.pdf', b'%PDF-1.7\nvalid', 'application/pdf')})
    assert response.status_code == 200
    metadata = response.json()['job_document']
    assert metadata['original_name'] == 'job.pdf'
    assert 'storage' not in str(metadata).lower() and 'sha256' not in metadata

    user.role, user.usn = 'student', 'P4'
    response = client.get(root)
    assert response.status_code == 200 and response.content.startswith(b'%PDF-')
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'private, no-store'

    user.role, user.usn = 'admin', None
    assert client.delete(root).status_code == 200
    assert client.get(root).status_code == 404
    assert list(tmp_path.glob('*.pdf')) == []


@pytest.mark.parametrize('filename,content_type,content,message', [
    ('job.txt', 'application/pdf', b'%PDF-1.7', '.pdf extension'),
    ('job.pdf', 'text/plain', b'%PDF-1.7', 'application/pdf'),
    ('job.pdf', 'application/pdf', b'not a pdf', 'not a PDF'),
    ('../job.pdf', 'application/pdf', b'%PDF-1.7', 'filename is invalid'),
])
def test_invalid_pdf_uploads_are_controlled(placement_client, monkeypatch, tmp_path,
                                             filename, content_type, content, message):
    client, user, drive_id, _ = placement_client
    user.id = 7
    monkeypatch.setattr(api, 'placement_document_storage',
                        lambda: LocalPlacementDocumentStorage(tmp_path, 1024))
    response = client.post(f'/api/placements/drives/{drive_id}/document',
                           files={'document': (filename, content, content_type)})
    assert response.status_code == 400 and message in response.json()['detail']
    assert list(tmp_path.iterdir()) == []


def test_oversized_pdf_and_document_authorization(placement_client, monkeypatch, tmp_path):
    client, user, drive_id, _ = placement_client
    user.id = 7
    monkeypatch.setattr(api, 'placement_document_storage',
                        lambda: LocalPlacementDocumentStorage(tmp_path, 8))
    root = f'/api/placements/drives/{drive_id}/document'
    response = client.post(root, files={'document': ('job.pdf', b'%PDF-1234', 'application/pdf')})
    assert response.status_code == 400 and 'limit' in response.json()['detail']
    for role in ('student', 'faculty', 'hod', 'principal'):
        user.role, user.usn = role, 'P4'
        assert client.post(root, files={'document': ('job.pdf', b'%PDF-', 'application/pdf')}).status_code == 403
        if role != 'student':
            assert client.get(root).status_code == 403
