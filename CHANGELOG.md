# Changelog

All notable changes to AI视频日记 Agent Studio.

## [0.1.0] — 2026-08-09

### Phase 0: Emergency Hardening
- **Worker crash protection**: `run_forever` loop now wrapped with try/except + 30s retry delay
- **Database write protection**: `_persist_episode` catches sqlite3.Error/OSError with explicit RuntimeError
- **Database read resilience**: `_load_database` skips corrupted JSON rows instead of failing entirely
- **Database field conversion**: `_episode_from_dict` uses safe defaults for int/date conversions
- **Global exception handler**: FastAPI `@app.exception_handler` returns safe JSON, no traceback leak
- **LLM/ASR retry**: DeepSeek HTTP calls get 3× exponential backoff; Tencent ASR gets 2× retry
- **Config template**: `.env.example` with all 15+ environment variables documented
- **LICENSE**: MIT License added

### Phase 1: First-Run Experience
- **Setup detection API**: `GET /api/is_setup` returns `{configured, missing, next_step}`
- **Welcome wizard**: 4-step onboarding (Welcome → API Key → Directories → Verify)
- **Settings page redesign**: 4 section cards (AI / Directories / ASR / External Sources) with directory picker
- **Hardcoded paths removed**: Developer-specific defaults replaced with `None`
- **Home redirect**: Unconfigured users redirected to `/welcome`
- **Health fix commands**: Each health check now includes a concrete fix command

### Phase 2: UX Foundation
- **Error boundaries**: `RouteError` component + 4 `error.tsx` files (root, script, video, edit-plan)
- **Loading skeletons**: 5 Skeleton component types + 8 `loading.tsx` files for all pages
- **Toast notifications**: `PixelToast` system with 30 server actions wired up
- **Form validation**: HTML5 client-side validation on all forms with `FormErrorBanner`
- **Fake data purge**: Hardcoded placeholder text removed from topics page; data-gated rendering
- **Disabled tooltips**: `PixelTooltip` component explaining why controls are disabled
- **Templates empty state**: Proper empty state component when no templates exist

### Phase 3: Product Completeness
- **Workflow progress bar**: Visual pipeline indicator (Topics → Script → Record → Edit → Export)
- **Keyboard shortcuts**: Ctrl+1~7 page navigation, ? for help, Ctrl+Shift+H for help toggle
- **In-app help panel**: Context-sensitive help for each page
- **Privacy policy + Terms of use**: Legal pages with local-first data handling explanation
- **Version endpoint**: `GET /api/version` with git commit info
- **Health feature gate**: LLM endpoints return 503 when API key is not configured
- **Safe duration fix**: `_safe_duration` now raises explicit error instead of silent 0.0

### Known Issues
- macOS desktop shell (Tauri/Electron) not yet packaged — requires terminal launch
- Jianying 10.7 native encrypted draft authoring not implemented (compatibility bridge only)
- Creator Profile (common expressions, banned words, style stabilizer) not yet implemented
- Voice commands: text input only; real microphone capture and ASR not yet integrated
- No auto-update mechanism
- Frontend test coverage: 0% (planned for Phase 5)
