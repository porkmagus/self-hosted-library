from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import IO

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.models import Base, Book, IngestionJob, get_db
from api.routers import upload


class Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_stream(
        self,
        object_name: str,
        data: IO[bytes],
        *,
        length: int,
        content_type: str,
    ) -> None:
        payload = data.read()
        assert len(payload) == length
        self.objects[object_name] = payload

    def stat(self, object_name: str) -> SimpleNamespace:
        return SimpleNamespace(size=len(self.objects[object_name]))

    def download_to(self, object_name: str, destination: Path) -> None:
        destination.write_bytes(self.objects[object_name])

    def delete(self, object_name: str) -> None:
        self.objects.pop(object_name, None)


def test_multipart_upload_is_api_mediated_and_queues_only_job_uuid(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def db_dependency() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    store = Store()
    monkeypatch.setattr(upload, "get_object_store", lambda: store)
    app = FastAPI()
    app.include_router(upload.router, prefix="/api")
    app.dependency_overrides[get_db] = db_dependency
    client = TestClient(app)

    response = client.post(
        "/api/upload",
        files={
            "file": ("Moon Magic.txt", b"long enough source contents", "text/plain")
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == body["job_uuid"]
    with factory() as session:
        book = session.query(Book).filter_by(uuid=body["book_uuid"]).one()
        job = session.query(IngestionJob).filter_by(uuid=body["job_uuid"]).one()
        assert book.source_object_key in store.objects
        assert job.book_id == book.id


def test_browser_facing_presign_routes_are_removed() -> None:
    paths = {route.path for route in upload.router.routes}

    assert "/upload/presign" not in paths
    assert "/upload/confirm" not in paths
