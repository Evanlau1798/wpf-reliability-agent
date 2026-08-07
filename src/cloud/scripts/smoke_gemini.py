import os

from app.model_smoke import create_vertex_client, model_smoke_target, run_model_smoke


def main() -> None:
    project, location, model = model_smoke_target(os.environ)
    client = create_vertex_client(project, location)
    result = run_model_smoke(client, model)
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
