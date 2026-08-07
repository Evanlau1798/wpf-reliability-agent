from types import SimpleNamespace

from app.model_smoke import model_smoke_target, run_model_smoke


class _Models:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed={"status": "ok"})


def test_model_smoke_requests_structured_json_response() -> None:
    models = _Models()
    client = SimpleNamespace(models=models)

    result = run_model_smoke(client, "gemini-test")

    assert result.status == "ok"
    assert models.calls[0]["model"] == "gemini-test"
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None


def test_model_smoke_target_requires_only_vertex_fields() -> None:
    target = model_smoke_target(
        {
            "GOOGLE_CLOUD_PROJECT": "demo-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "GEMINI_MODEL": "gemini-test",
        }
    )

    assert target == ("demo-project", "us-central1", "gemini-test")
