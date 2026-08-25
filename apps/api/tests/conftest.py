import os

# Set a fake API key so feature gates don't block LLM endpoints in tests.
# Tests use MissingScriptGenerator/TemplateScriptGenerator as fallback.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-fake-key-for-tests")

# Unit tests default to the local single-user boundary even when a developer
# has real Supabase credentials in apps/api/.env. Auth-focused tests inject an
# explicit Settings instance, so they still exercise the trusted boundary.
os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "")
os.environ.setdefault("SUPABASE_JWT_ISSUER", "")
