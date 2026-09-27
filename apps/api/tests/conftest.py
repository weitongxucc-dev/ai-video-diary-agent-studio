import os
from pathlib import Path
from tempfile import TemporaryDirectory

# app.main initializes repositories during import, before per-test monkeypatches.
# Keep those initializers away from every developer/app database and output tree.
_test_directory = TemporaryDirectory(prefix="ai-video-tests-", dir=os.environ.get("TMPDIR"))
_test_root = Path(_test_directory.name)
(_test_root / "empty.env").touch()
for name, relative in {
    "DATABASE_PATH": "data/studio.sqlite3",
    "VIDEO_INBOX_PATH": "inbox",
    "VIDEO_OUTPUT_PATH": "output",
    "DRAFT_OUTPUT_PATH": "drafts",
    "JIANYING_DRAFT_ROOT_PATH": "jianying",
    "MODEL_ROOT_PATH": "models",
    "TEMPORARY_ROOT_PATH": "temporary",
}.items():
    path = _test_root / relative
    (path.parent if name == "DATABASE_PATH" else path).mkdir(parents=True, exist_ok=True)
    os.environ[name] = str(path)
os.environ.update({
    "AI_VIDEO_DIARY_DOTENV": str(_test_root / "empty.env"),
    "NATIVE_OCR_WORKER_PATH": "",
    "NATIVE_PORTRAIT_WORKER_PATH": "",
    "LLM_PROVIDER": "deepseek",
    "LLM_API_KEY": "test-only-key",
    "LLM_API_KEY_PROVIDER": "deepseek",
    "LLM_BASE_URL": "http://127.0.0.1:9",
    "DEEPSEEK_API_KEY": "test-only-key",
    "DEEPSEEK_BASE_URL": "http://127.0.0.1:9",
    "TENCENTCLOUD_SECRET_ID": "",
    "TENCENTCLOUD_SECRET_KEY": "",
    "PRODUCT_HUNT_TOKEN": "",
    "SUPABASE_URL": "",
    "SUPABASE_PUBLISHABLE_KEY": "",
    "SUPABASE_JWT_ISSUER": "",
})


def pytest_sessionfinish(session, exitstatus):
    _test_directory.cleanup()
