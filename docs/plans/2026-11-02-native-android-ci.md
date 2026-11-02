# CI and Native Android Remote Implementation Plan

> **For implementation:** Complete each task with focused tests and verification; do not mark a parity item complete without an end-to-end check against the backend.

**Goal:** Repair the failing CI and deliver a fully native Android remote for every DiakoPad web feature on a local network, with a reproducible APK build.

**Architecture:** The existing FastAPI service remains the source of truth and the audio engine. A Kotlin/Jetpack Compose application uses typed REST commands and a WebSocket state stream, with no WebView. Authentication must work for both channels. Android does not produce audio locally.

**Tech stack:** Python 3.12, Ruff, unittest, GitHub Actions; Kotlin, Jetpack Compose, Android Gradle Plugin, OkHttp, org.json, Android SDK.

---

## Baseline and constraints

- Branch: `feature/native-android-ci` in an isolated worktree.
- Current CI failure reproduced with `uvx --from ruff==0.15.0 ruff check backend --no-cache`: three findings in `backend/tests/test_scene_engine.py` and `backend/tests/test_sfz.py`. GitHub Actions stops at lint, so test status is unknown.
- Web UI feature inventory: `frontend/index.html` and `frontend/app.js`; HTTP routes and `/ws`: `backend/app.py`. Audio remains server-side.
- Scope includes pads/performance, scenes, kits/library/uploads, per-pad and shared FX, mixing/master, sequencer/patterns, tempo/metronome, four-track looper, MIDI/knobs/controller learning, settings, engine/system and PANIC.
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
2. Implement 16-pad grid, triggering, pad assignment and editing, performance carousel, active scenes, scene save/delete/load, PANIC confirmation, global tempo/tap, master/mute and engine status/restart.
3. Verify REST commands, WebSocket updates, device rotation and disconnected/reconnected state.

## Task 4: Native sound, mixing and effects parity

1. Implement sound browsing/search/preview, folder-aware upload and deletion, kit import/create/save/load/category browsing.
2. Implement per-pad volume/pan/filter/tune, master compressor/limiter, local effect slots/parameters and shared FX buses/sends.
3. Test a complete change-to-audible-state flow on a running DiakoPad, including large sound libraries and uploads.

## Task 5: Native transport, MIDI and settings parity

1. Implement sequencer grid, transport and patterns; BPM, signature, metronome style/transport; four looper tracks, record/overdub/play/stop/clear/mute/volume and quantization.
2. Implement MIDI note mapping and learning, CC knob mapping and learning, controller actions and learning, remaining settings and guarded system restart.
3. Compare every control and state in `frontend/index.html` and `frontend/app.js` against a native screen and API contract; fill gaps in backend with test-first changes only when needed.

## Task 6: Acceptance and release

1. Run Ruff, backend/frontend unittest suites, ShellCheck, Android unit/instrumentation tests and `assembleDebug` in CI.
2. Install a debug APK on an Android device and test against Ubuntu Studio and Raspberry Pi on the same Wi-Fi: authentication, reconnection, WebSocket sync, file picker/upload, all controls, and PANIC.
3. Document installation, network/token setup and LAN HTTP risks in `README.md`. Release APK requires a separately secured signing key; never commit it.

**Done means:** All parity checks and test commands pass, a build artifact exists, and both a local Android device and the GitHub workflow verify it. Until then, report the completed subset explicitly rather than calling the whole app finished.

## Execution checkpoint (2026-11-02)

- Task 1: Ruff errors corrected; isolated database fixtures fixed eight test failures revealed after lint; 249 backend tests passed locally on Python 3.14. Frontend test step added; two stale test expectations updated and all 27 frontend tests pass locally. GitHub run pending.
- Task 2: Android Gradle wrapper and project created; LAN address and cookie-authenticated client have unit tests (red/green verified). Android CI job added; APK builds locally using Android SDK 35; a device test is pending. Token is held in memory, not persisted.
- Task 3: Initial native controls for pads, scene load/save/activation, sound assignment, basic pad mix, BPM, sequencer/metronome/looper transports, master and confirmed PANIC. **Not full parity**: scene deletion, full pad editing, and several operations remain.
- Tasks 4–6: Not completed. Do not distribute the debug APK as a fully functional release.
