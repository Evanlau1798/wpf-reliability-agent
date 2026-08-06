from pydantic import SecretStr

from app.config import Settings


def test_settings_model_contains_only_gate_eight_runtime_values() -> None:
    settings = Settings(
        service_role="api",
        google_cloud_project="project-test",
        demo_device_id="device-test",
        demo_device_token="secret-token",
    )

    assert set(Settings.model_fields) == {
        "service_role",
        "google_cloud_project",
        "demo_device_id",
        "demo_device_token",
    }
    assert settings.service_role == "api"
    assert isinstance(settings.demo_device_token, SecretStr)
    assert "secret-token" not in repr(settings)
