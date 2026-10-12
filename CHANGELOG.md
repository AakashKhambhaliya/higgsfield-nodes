# Changelog

All notable changes to this project. Versions follow [semantic versioning](https://semver.org):
MAJOR = breaking change, MINOR = new features, PATCH = fixes.
To release: update `version` in `pyproject.toml` **and** `higgsfield_nodes/__init__.py`, add an entry here,
push to `main` — GitHub Actions publishes it to the Comfy Registry and users see the update in ComfyUI Manager.

## 0.9.0 — first public beta

- One node per Higgsfield model family (32 families, 85 endpoints) generated from Higgsfield's official
  JSON schemas, plus GPT Image 2.5 and Soul ID (train / list) nodes.
- Live price badge on every node (Higgsfield estimate with an API key, published range without).
- Cost safety: estimate-only runs, per-node max cost, idempotent submissions, results saved locally.
- Preset / style / Soul ID pickers (right-click a node).
- Request validation against the full schema (incl. conditional rules) before uploading or paying.
- Settings → Higgsfield: API key panel (encrypted on Windows), model catalog updates (daily + on demand),
  pack update notice.
- End-to-end error handling: every failure says which step failed and whether you were charged.
- Six example workflows in ComfyUI's template browser.
- Works on any ComfyUI ≥ 0.14.0, local or cloud; `HF_KEY` environment variable for servers.
