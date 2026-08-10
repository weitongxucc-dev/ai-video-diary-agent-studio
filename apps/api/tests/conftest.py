import os

# Set a fake API key so feature gates don't block LLM endpoints in tests.
# Tests use MissingScriptGenerator/TemplateScriptGenerator as fallback.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-fake-key-for-tests")
