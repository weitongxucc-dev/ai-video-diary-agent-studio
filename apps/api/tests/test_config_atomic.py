from pathlib import Path

from app import config
from app.config import write_dotenv_values


def test_write_dotenv_values_replaces_file_atomically(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DEEPSEEK_MODEL=old-model\nUNCHANGED=yes\n",
        encoding="utf-8",
    )
    replacements: list[tuple[Path, Path]] = []
    real_replace = config.os.replace

    def capture_replace(source, target):
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.exists()
        assert source_path.parent == env_path.parent
        assert source_path.name.startswith(".env.")
        assert source_path.read_text(encoding="utf-8") == (
            "DEEPSEEK_MODEL=new-model\n"
            "UNCHANGED=yes\n"
            "DEEPSEEK_API_KEY=secret\n"
        )
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr(config.os, "replace", capture_replace)

    write_dotenv_values(
        env_path,
        {
            "DEEPSEEK_MODEL": "new-model",
            "DEEPSEEK_API_KEY": "secret",
        },
    )

    assert len(replacements) == 1
    assert replacements[0][1] == env_path
    assert env_path.read_text(encoding="utf-8") == (
        "DEEPSEEK_MODEL=new-model\n"
        "UNCHANGED=yes\n"
        "DEEPSEEK_API_KEY=secret\n"
    )
