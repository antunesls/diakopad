# CI and Native Android Remote Implementation Plan

> **For implementation:** Complete each task with focused tests and verification; do not mark a parity item complete without an end-to-end check against the backend.

**Goal:** Repair the failing CI and deliver a fully native Android remote on a local network, with a reproducible APK build. The app can configure and perform with existing sounds but does not transfer audio files.

**Architecture:** The existing FastAPI service remains the source of truth and the audio engine. A Kotlin/Jetpack Compose application uses typed REST commands and a WebSocket state stream, with no WebView. Authentication must work for both channels. Android does not produce audio locally.

**Tech stack:** Python 3.12, Ruff, unittest, GitHub Actions; Kotlin, Jetpack Compose, Android Gradle Plugin, OkHttp, org.json, Android SDK.

---

## Baseline and constraints

- Branch: `feature/native-android-ci` in an isolated worktree.
- Current CI failure reproduced with `uvx --from ruff==0.15.0 ruff check backend --no-cache`: three findings in `backend/tests/test_scene_engine.py` and `backend/tests/test_sfz.py`. GitHub Actions stops at lint, so test status is unknown.
- Web UI feature inventory: `frontend/index.html` and `frontend/app.js`; HTTP routes and `/ws`: `backend/app.py`. Audio remains server-side.
- Scope includes pads and assignment from existing sounds, creation/loading/editing of kits, creation/activation/loading/deletion of scenes, per-pad/shared FX, mixing, master, sequencer/patterns, tempo/metronome, four-track looper, settings, engine status and PANIC. The backend's MIDI controller remains on the laptop/Pi.
- Explicitly out of scope for Android: uploading/importing new audio or ZIP files and managing file/folder storage. The native app can browse and assign sounds already present on the server. MIDI mapping can remain a later enhancement, but is not excluded by this clarification.
- LAN HTTP is unencrypted. Require a configured token for remote access, do not expose the service to the internet, and do not log credentials.

## Task 1: Unblock and expand CI

**Files:** `.github/workflows/ci.yml`, `backend/tests/test_scene_engine.py`, `backend/tests/test_sfz.py`.

1. Record the Ruff failures, change only the offending test import/variable names, run the same Ruff version again.
2. Run backend unittest discovery (Python 3.12 with locked requirements) and diagnose any further failure before fixing it.
3. Add a frontend unittest discovery step for `frontend/tests`; retain ShellCheck. Verify locally and in a GitHub runner when available.
4. Update deprecated Actions versions independently of lint repair if the versions are compatible.

## Task 2: Native project and testable network client

**Files:** `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml`, `android/app/src/main/java/...`, `android/app/src/test/java/...`, `.gitignore`.

1. Add Android project with reproducible Gradle wrapper, application ID, min/target SDK, compile dependencies and CI assemble/test job.
2. Test URL validation and path joining, token handling, HTTP error handling, WebSocket authentication and reconnect behavior before implementing clients.
3. Build an API client with typed models for state and commands, a repository that merges REST bootstrap with WebSocket updates, lifecycle-aware reconnect and explicit offline state.
4. Store connection configuration securely; send credentials without putting tokens into persistent URLs or logs. Restrict navigation/requests to the configured LAN host.

## Task 3: Native stage and performance screens

**Files:** `android/app/src/main/java/.../ui/` and matching tests.

1. Implement connection/setup with host, token and live status.
2. Implement 16-pad grid, triggering and assignment of existing sounds, scene create/activate/load/delete, kit create/from-pads/load, PANIC confirmation, global tempo/tap, master/mute and engine status.
3. Verify REST commands, WebSocket updates, device rotation and disconnected/reconnected state.

## Task 4: Native kit, mixing and effects editing

1. Implement kit creation/editing from existing samples (and save from current pads), kit selection and deletion with confirmation. No upload/import.
2. Implement per-pad volume/pan/filter/tune, master compressor/limiter, shared FX buses/sends using touch-friendly sliders, and per-pad effect slots/catalog/parameters.
3. Test a complete change-to-audible-state flow on a running DiakoPad.

## Task 5: Native transport parity

1. Implement sequencer playback/grid and existing pattern selection, BPM/tap, signature and metronome style/transport.
2. Implement four looper tracks: record/overdub/play/stop, track selection/mute/volume and guarded clear.
3. Compare all in-scope controls and states in `frontend/index.html` and `frontend/app.js` against a native screen and API contract. Explicitly exclude upload/import and file/folder maintenance.

## Task 6: Acceptance and release

1. Run Ruff, backend/frontend unittest suites, ShellCheck, Android unit/instrumentation tests and `assembleDebug` in CI.
2. Install a debug APK on an Android device and test against Ubuntu Studio and Raspberry Pi on the same Wi-Fi: authentication, reconnection, WebSocket sync, all stage controls, and PANIC.
3. Document installation, network/token setup and LAN HTTP risks in `README.md`. Release APK requires a separately secured signing key; never commit it.

**Done means:** All in-scope live controls and test commands pass, a build artifact exists, and both a local Android device and the GitHub workflow verify it. Until then, report the completed subset explicitly rather than calling the whole app finished.

## Execution checkpoint (2026-11-02)

- Task 1: Ruff errors corrected; isolated database fixtures fixed eight test failures revealed after lint; 249 backend tests passed locally on Python 3.14. Frontend test step added; two stale test expectations updated and all 27 frontend tests pass locally. GitHub run pending.
- Task 2: Android Gradle wrapper and project created; LAN address and cookie-authenticated client have unit tests (red/green verified). Android CI job added; APK builds locally using Android SDK 35; a device test is pending. Token is held in memory, not persisted.
- Task 3: Initial native controls for pads, scene create/load/activation/delete, sound assignment, kit assembly from existing sounds/create/load/delete, basic pad mix, per-pad and shared FX, BPM, sequencer/metronome/looper transports, master and confirmed PANIC. **Not full parity**: advanced editing and transport remain.
- Tasks 4–6: In progress. Kit/scenes/effects management (including selectable FX parameter options), existing sound assignment, sequencer grid/patterns, metronome controls, looper tracks and master controls have initial native screens. File upload/import and folder management are out of scope. MIDI mapping, live device tests and GitHub CI checks are still pending. Do not distribute the debug APK as a fully functional release.

## GitHub Releases checkpoint

- A tag-triggered workflow builds a signed APK, verifies signature/package/version and creates a GitHub Release only for tags on `main` after backend, frontend, ShellCheck and Android tests.
- Tag-to-Android-version calculation has its own tests. Local build/signature verification was performed using a disposable one-day test keystore; the test key and APK were removed afterward.
- External prerequisites before first publication: configure the four release-signing repository secrets, merge the branch to `main`, create/push a new `vMAJOR.MINOR.PATCH` tag and check the remote Actions run. Do not claim a published release until those steps are verified.
