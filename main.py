from pathlib import Path

from waitress import serve

from neontag.web import create_app


if __name__ == "__main__":
    project = Path(__file__).resolve().parent
    (project / "models").mkdir(exist_ok=True)
    app = create_app(project)
    print("NeonTag · http://0.0.0.0:8080", flush=True)
    serve(app, host="0.0.0.0", port=8080, threads=8)
