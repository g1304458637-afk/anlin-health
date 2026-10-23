"""A development preview or local credential must never enter a release archive."""
import importlib.util
from pathlib import Path


def test_release_excludes_local_credentials_runtime_and_database_sidecars(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "ops/make_release.py"
    spec = importlib.util.spec_from_file_location("anlin_release", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for name in ["backend/.env", "backend/.env.local", "backend/.env.production", ".runtime/start_preview.py",
                 ".runtime/preview.db", "backend/residents.db-wal", "backend/residents.db-shm", "backend/backup.sqlite",
                 "private_reports/opaque", "backend/private_reports/opaque"]:
        assert not module.allowed(tmp_path / name), name
    assert module.allowed(tmp_path / "backend/.env.example")
    assert module.allowed(tmp_path / "backend/app/main.py")
