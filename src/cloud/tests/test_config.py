from pydantic import SecretStr
import pytest

from app.config import Settings


def test_settings_model_contains_only_runtime_values() -> None:
    settings = Settings(
        service_role="api",
        google_cloud_project="project-test",
        demo_device_id="device-test",
        demo_device_token="secret-token",
        demo_operator_token="operator-secret",
        pubsub_topic="incident-work",
    )

    assert set(Settings.model_fields) == {
        "service_role",
        "google_cloud_project",
        "demo_device_id",
        "demo_device_token",
        "demo_operator_token",
        "pubsub_topic",
        "pubsub_push_audience",
        "pubsub_invoker_email",
        "gemini_model",
        "google_cloud_location",
    }
    assert settings.service_role == "api"
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert isinstance(settings.demo_device_token, SecretStr)
    assert isinstance(settings.demo_operator_token, SecretStr)
    assert "secret-token" not in repr(settings)
    assert "operator-secret" not in repr(settings)


def test_settings_load_from_environment_names() -> None:
    settings = Settings.from_environment(
        {
            "SERVICE_ROLE": "worker",
            "GOOGLE_CLOUD_PROJECT": "project-test",
            "DEMO_DEVICE_ID": "device-test",
            "DEMO_DEVICE_TOKEN": "secret-token",
            "PUBSUB_TOPIC": "incident-work",
            "PUBSUB_PUSH_AUDIENCE": "https://worker.example.test",
            "PUBSUB_INVOKER_EMAIL": "pubsub-invoker@example.test",
            "GOOGLE_CLOUD_LOCATION": "asia-east1",
        }
    )

    assert settings.service_role == "worker"
    assert settings.google_cloud_project == "project-test"
    assert settings.pubsub_topic == "incident-work"
    assert settings.pubsub_push_audience == "https://worker.example.test"
    assert settings.pubsub_invoker_email == "pubsub-invoker@example.test"
    assert settings.google_cloud_location == "asia-east1"


def test_gemini_model_can_override_the_documented_default() -> None:
    settings = Settings.from_environment(
        {
            "SERVICE_ROLE": "api",
            "GOOGLE_CLOUD_PROJECT": "project-test",
            "DEMO_DEVICE_ID": "device-test",
            "DEMO_DEVICE_TOKEN": "secret-token",
            "DEMO_OPERATOR_TOKEN": "operator-secret",
            "PUBSUB_TOPIC": "incident-work",
            "GEMINI_MODEL": "gemini-region-smoke-test",
        }
    )

    assert settings.gemini_model == "gemini-region-smoke-test"


def test_missing_settings_report_environment_variable_names() -> None:
    with pytest.raises(
        ValueError,
        match="GOOGLE_CLOUD_PROJECT, DEMO_DEVICE_ID, DEMO_DEVICE_TOKEN, PUBSUB_TOPIC",
    ):
        Settings.from_environment({"SERVICE_ROLE": "api"})


def test_worker_settings_require_pubsub_identity_values() -> None:
    with pytest.raises(
        ValueError,
        match="PUBSUB_PUSH_AUDIENCE, PUBSUB_INVOKER_EMAIL, GOOGLE_CLOUD_LOCATION",
    ):
        Settings.from_environment(
            {
                "SERVICE_ROLE": "worker",
                "GOOGLE_CLOUD_PROJECT": "project-test",
                "DEMO_DEVICE_ID": "device-test",
                "DEMO_DEVICE_TOKEN": "secret-token",
                "PUBSUB_TOPIC": "incident-work",
            }
        )
