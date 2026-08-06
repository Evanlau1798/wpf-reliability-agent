from pydantic import SecretStr
import pytest

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


def test_settings_load_from_environment_names() -> None:
    settings = Settings.from_environment(
        {
            "SERVICE_ROLE": "worker",
            "GOOGLE_CLOUD_PROJECT": "project-test",
            "DEMO_DEVICE_ID": "device-test",
            "DEMO_DEVICE_TOKEN": "secret-token",
        }
    )

    assert settings.service_role == "worker"
    assert settings.google_cloud_project == "project-test"


def test_missing_settings_report_environment_variable_names() -> None:
    with pytest.raises(
        ValueError,
        match="GOOGLE_CLOUD_PROJECT, DEMO_DEVICE_ID, DEMO_DEVICE_TOKEN",
    ):
        Settings.from_environment({"SERVICE_ROLE": "api"})
