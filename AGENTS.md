# AGENTS.md

## Runtime And Commands

- This is a Frappe/ERPNext v16 app, not a standalone Python service. `pyproject.toml` allows Frappe and ERPNext `>=16,<17`; the image workflow currently pins Frappe `v16.27.0` and ERPNext `v16.28.0`.
- Run Frappe commands from a Bench where `erpverein` is installed. There is no repository-local lint, formatter, typecheck, or standalone test configuration.
- The rename to package/app identity `erpverein` is an identity reset. Only clean-site installation is supported; old development sites from before the rename are explicitly unsupported and must not be migrated or treated as an update path.
- Fresh-site verification order:

```bash
bench --site <site> install-app erpverein
bench --site <site> migrate
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --module erpnext.tests.bootstrap_test_data --lightmode
bench --site <site> run-tests --app erpverein
```

- The current preproduction baseline is patch-free and supports clean-site installation only. Recreate development sites instead of updating them. After the production baseline is declared, preserve all subsequently released patches as immutable migration history.

- Run one test module with `bench --site <site> run-tests --app erpverein --module erpverein.tests.test_<name>`, for example `erpverein.tests.test_mieter_doctype`.
- `hooks.py` runs `erpverein.tests.before_tests.before_tests`, which syncs app fields and small app-owned setup masters. Billing integration tests additionally require ERPNext's supported bootstrap data command shown above.

## Package Boundaries

- App-owned DocTypes are under `erpverein/erpverein/doctype/`; their JSON is schema/UI metadata, controllers contain lifecycle wiring, and reusable behavior belongs in `erpverein/services/`.
- `erpverein/api/` contains thin whitelisted wrappers. Keep permission checks and business logic in services; subscription creation is queued on `long` with `enqueue_after_commit=True`.
- `erpverein/hooks.py` is wiring only. Its `Customer` events maintain the reciprocal Mitglied and Mieter links; do not move business logic into hooks.
- `Mitglied` and `Mieter` are app-owned master records with permission-checked 1:1 links to ERPNext `Customer`.
- Non-Administrator operators need the intersecting standard roles for affected records: `System Manager` for app DocTypes, `Sales Master Manager`/`Sales User` for Customer/Address/Contact writes, and `Accounts User` or `Accounts Manager` for Bank Account and Subscription operations. Do not bypass a missing role in code.
- `SEPA Mandat` validates mandate state and synchronizes app-managed data to ERPNext `Bank Account`; changes touch sensitive banking data and reciprocal links.
- `Beitragsabrechnung` and `Mietabrechnung` are non-submittable preview/run DocTypes. Their services create ERPNext `Subscription` records asynchronously; they do not post GL entries directly.
- The Startseite UI has two durable sources: `erpverein/erpverein/workspace/startseite/startseite.json` and `erpverein/workspace_sidebar/startseite.json`. Existing sites need a patch to re-import changed workspace metadata.

## Custom Fields And Migrations

- Never edit Frappe or ERPNext core and do not rely on manual Desk customization for durable behavior.
- `erpverein/custom_fields.py` is the source of truth for app-owned fields on ERPNext DocTypes. `install.py` syncs those fields and setup data for fresh installs; an idempotent registered patch must handle existing sites.
- Do not add migration patches before the production baseline is declared; make direct source changes and verify them on a clean site. After production starts, register migrations in `erpverein/patches.txt`, preserve released patch behavior, and cover both fresh-install and update paths.
- Do not add broad Custom Field fixtures. Install, uninstall, patches, and tests must affect only fields owned by this app.
- Do not call `frappe.db.commit()` from controllers, services, hooks, APIs, or patches; Bench/Frappe owns transaction boundaries.

## Permissions And Data Safety

- User-triggered reads should use permission-aware APIs such as `frappe.db.get_list`, not `frappe.get_all`.
- Cross-DocType synchronization may use low-level writes only after checking write permission on every affected document. Preserve the existing recursion-safe `set_value_if_changed` style.
- Keep `ignore_permissions=True` limited to tests, install/setup, patches, or explicit system operations; never use it to make a whitelisted method succeed.
- Before migration-bearing production deploys, preserve a backup with files and `site_config.json`, including `encryption_key`. Git rollback cannot reverse migrated data.
- `.env`, `.private/`, and `.opencode/` are ignored local state and must not be committed; never add dumps, backups, credentials, or site secrets.

## Tests And Releases

- Use `frappe.tests.UnitTestCase` for pure helpers and `IntegrationTestCase` for DB-backed DocType, permission, patch, and ERPNext integration behavior.
- Changes to standard-DocType extensions need Custom Field metadata tests; reciprocal links need tests from both sides; patches need first-run and rerun tests.
- `.github/workflows/build-image.yml` builds and loads the exact custom `linux/amd64` image, creates a clean site with pinned components, installs ERPNext and `erpverein`, migrates, and runs `bench --site ci.localhost run-tests --app erpverein` before any push. The same locally verified image is then pushed to GHCR.
- Release/image tags must match `erpverein-v<erpnext-version>-<app-version>`, for example `erpverein-v16.28.0-0.1.7`. Tag pushes derive the ERPNext tag and app ref from that release tag while keeping Frappe and `frappe_docker` pinned; manual image tags are sanitized.
- Before tagging, reconcile the app version in `pyproject.toml`, `erpverein/__init__.py`, and `README.md`. The workflow rejects a mismatch between either version source and the release tag.
- The published image name is `ghcr.io/<owner>/erpverein`; no image is published unless the clean-site install, migrate, and complete app test gate succeeds.
- Production uses one custom GHCR image across backend, frontend, websocket, scheduler, and both queue workers. Deploy the same image tag everywhere, then migrate and smoke-test Customer links, mandates, both billing runs, scheduler, and queues.
