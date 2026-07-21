import io

import pytest
import torch
from PIL import Image

from api.services import embedding_client, embedding_server


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(output, format="PNG")
    return output.getvalue()


def test_clip_client_posts_raw_image_bytes(monkeypatch) -> None:
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"embedding": [0.0] * 512}

    class Client:
        def post(self, path: str, *, content: bytes, headers: dict[str, str]) -> Response:
            calls.append((path, content, headers))
            return Response()

    monkeypatch.setattr(embedding_client, "_get_client", lambda: Client())

    assert embedding_client.get_clip_image_embedding(b"image") == [0.0] * 512
    assert calls == [
        ("/embed-image", b"image", {"content-type": "application/octet-stream"})
    ]


def test_clip_server_normalizes_image_embedding(monkeypatch) -> None:
    class Processor:
        def __call__(self, *, images, return_tensors: str):
            assert len(images) == 1
            assert return_tensors == "pt"
            return {"pixel_values": torch.ones((1, 1))}

    class Model:
        device = torch.device("cpu")

        @staticmethod
        def vision_model(**inputs):
            assert "pixel_values" in inputs
            return type("VisionOutput", (), {"pooler_output": torch.tensor([[3.0, 4.0]])})()

        @staticmethod
        def visual_projection(value):
            return value

    monkeypatch.setattr(embedding_server, "_clip_processor", Processor())
    monkeypatch.setattr(embedding_server, "_clip_model", Model())

    assert embedding_server._do_embed_image(_png_bytes()) == pytest.approx([0.6, 0.8])
