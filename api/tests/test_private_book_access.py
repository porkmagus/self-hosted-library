from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from api.models import Base, Book, BookStatus
from api.routers import book_viewer, ingest


def _sessions() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_book_detail_does_not_expose_private_object_key(monkeypatch) -> None:
    factory = _sessions()
    with factory() as session:
        session.add(
            Book(
                uuid="11111111-1111-1111-1111-111111111111",
                title="Private",
                original_filename="private.pdf",
                sanitized_filename="private.pdf",
                file_extension=".pdf",
                source_object_key="books/private/source.pdf",
                status=BookStatus.PENDING,
            )
        )
        session.commit()

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    monkeypatch.setattr(ingest, "get_db_session", sessions)
    payload = ingest.get_book("11111111-1111-1111-1111-111111111111")

    assert "source_object_key" not in payload


def test_soft_deleted_book_source_cannot_be_streamed(monkeypatch) -> None:
    factory = _sessions()
    with factory() as session:
        session.add(
            Book(
                uuid="22222222-2222-2222-2222-222222222222",
                title="Deleted",
                original_filename="deleted.pdf",
                sanitized_filename="deleted.pdf",
                file_extension=".pdf",
                source_object_key="books/deleted/source.pdf",
                status=BookStatus.DELETED,
                deleted_at=session.execute(select(func.now())).scalar_one(),
            )
        )
        session.commit()

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    monkeypatch.setattr(book_viewer, "get_db_session", sessions)
    monkeypatch.setattr(
        book_viewer,
        "get_object_store",
        lambda: (_ for _ in ()).throw(AssertionError("storage must not be touched")),
    )
    with pytest.raises(HTTPException) as exc:
        book_viewer.get_book_pdf("22222222-2222-2222-2222-222222222222")
    assert exc.value.status_code == 404


def test_image_stream_requires_current_indexed_generation(monkeypatch) -> None:
    factory = _sessions()
    with factory() as session:
        book = Book(
            uuid="33333333-3333-3333-3333-333333333333",
            title="Indexed",
            original_filename="indexed.pdf",
            sanitized_filename="indexed.pdf",
            file_extension=".pdf",
            source_object_key="books/indexed/source.pdf",
            status=BookStatus.INDEXED,
            indexed_generation=4,
        )
        session.add(book)
        session.commit()
        book_uuid = book.uuid

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    monkeypatch.setattr(book_viewer, "get_db_session", sessions)
    monkeypatch.setattr(
        book_viewer,
        "get_object_store",
        lambda: (_ for _ in ()).throw(AssertionError("storage must not be touched")),
    )

    with pytest.raises(HTTPException) as exc:
        book_viewer.get_book_image(
            book_uuid,
            3,
            "11111111-1111-1111-1111-111111111111",
            "png",
        )

    assert exc.value.status_code == 404
