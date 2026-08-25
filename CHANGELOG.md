# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0-beta] - 2026-08-25

### Added
- **Beta Release:** Feature-complete public beta of the `mt5-guardrail` REST API bridge. Ready for community testing and feedback.
- Core cross-OS communication allowing Linux/WSL clients to securely execute commands on MetaTrader 5 (Windows).
- FastAPI server implementation for high-speed HTTP request processing.
- API Key Authentication system to secure endpoints from unauthorized access.
- Environment variable management setup (via `.env`) to prevent hardcoded secrets.
- Comprehensive `README.md` with cross-OS installation instructions and usage examples.
- Standardized open-source structure with Apache License 2.0.

### Security
- Integrated Security Headers Middleware to protect against common web vulnerabilities (e.g., CSRF, XSS).
- Mitigated CSV/Formula Injection (CWE-1236) vulnerability by properly sanitizing data inputs/outputs.
- Sanitized entire codebase to ensure no personal logic, private account numbers, or hardcoded API keys are exposed.