# AGENTS.md

This file provides guidance on how to work with this repository.

## Project Overview

`verein_erp` is an ERPNext/Frappe custom app for Vereinsverwaltung. It extends ERPNext in an update-friendly way: own business objects live as app DocTypes, ERPNext standard DocTypes are extended through code-managed Custom Fields, and every version must be both fresh-installable and updateable.

Target runtime for the first production line:

- Frappe: `v16.25.0`
- ERPNext: `v16.26.2`
- App: `verein_erp` `0.1.4`
- Deployment model: custom ERPNext image via GitHub Actions/GHCR, deployed later on `host01` with rootless Podman Quadlet

## General Guidelines

- Never edit ERPNext or Frappe core.
- Durable behavior belongs in this app, not in manual UI-only customizations.
- Keep changes small, boring, reproducible, and migration-safe.
- Every release must work on a clean install and as an update on an existing site.
- Do not uninstall/reinstall for normal updates.
- Custom Fields on ERPNext standard DocTypes must be managed in `verein_erp/custom_fields.py` plus install and patch paths.
- Patches must be idempotent and historically stable after release.
- `hooks.py` is wiring only; business logic belongs in controllers or services.
- Avoid comments unless they explain why a non-obvious choice is safe.
- Use ASCII in code and docs unless the file already requires German text.

## Essential Commands

Run these from a Bench environment where `verein_erp` is available as an app.

Fresh install on a site:

```bash
bench --site <site> install-app verein_erp
bench --site <site> migrate
bench --site <site> run-tests --app verein_erp
```

Normal update on an already installed site:

```bash
bench --site <site> backup --with-files
bench --site <site> migrate
bench --site <site> run-tests --app verein_erp
```

Focused tests:

```bash
bench --site <site> run-tests --app verein_erp --module verein_erp.tests.test_custom_fields
bench --site <site> run-tests --app verein_erp --module verein_erp.tests.test_mitglied_doctype
bench --site <site> run-tests --app verein_erp --module verein_erp.tests.test_patch_p0001_sync_mitglied_custom_fields
```

Static local checks without Bench:

```bash
python - <<'PY'
import ast
from pathlib import Path
for path in Path('verein_erp').rglob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
PY
python -m json.tool verein_erp/verein_erp/doctype/mitglied/mitglied.json >/tmp/mitglied.json.validated
python -c 'import tomllib; tomllib.load(open("pyproject.toml", "rb"))'
```

## Repository Structure

Key files and responsibilities:

- `pyproject.toml`: installable Python/Frappe package metadata.
- `README.md`: human-facing app and operations summary.
- `verein_erp/hooks.py`: Frappe hook wiring only.
- `verein_erp/install.py`: fresh-install lifecycle; creates app-owned Custom Fields.
- `verein_erp/uninstall.py`: uninstall lifecycle; removes only app-owned Custom Fields.
- `verein_erp/custom_fields.py`: source of truth for ERPNext standard DocType Custom Fields.
- `verein_erp/patches.txt`: ordered patch registry.
- `verein_erp/patches/v0_1/`: versioned, idempotent migration patches.
- `verein_erp/services/`: reusable business logic and cross-DocType sync logic.
- `verein_erp/api/`: future thin whitelisted wrappers only; no heavy business logic.
- `verein_erp/tests/`: Frappe tests for fields, patches, services, and DocTypes.
- `verein_erp/verein_erp/doctype/mitglied/`: app-owned `Mitglied` DocType JSON, controller, and form JS.

## Architecture Overview

The first vertical slice is `Mitglied`.

- `Mitglied` is an app-owned, non-submittable DocType for member master data.
- `Mitglied` is not an accounting document and must not create GL entries or invoices directly.
- `Customer.mitglied` is a code-managed Custom Field on ERPNext `Customer`.
- `Mitglied.customer` and `Customer.mitglied` form a server-side validated 1:1 relationship.
- Link fields in Frappe are searchable combobox-style fields by default.
- Bank account data is intentionally not duplicated in `Mitglied`; use ERPNext Customer/Bank Account mechanisms.
- `Mitglied` stores only mandate metadata such as `mandat_id` and `mandatsdatum`.

## Key Development Patterns

- Own durable business objects: app DocTypes under `verein_erp/verein_erp/doctype/`.
- ERPNext standard DocType extensions: `verein_erp/custom_fields.py`, `install.py`, and patches.
- Business logic: `verein_erp/services/` or the own DocType controller.
- Standard DocType events: narrow `doc_events` in `hooks.py` pointing to service functions.
- UI convenience for own DocTypes: DocType-local `.js` files only.
- Data/schema transitions: versioned patches under `verein_erp/patches/vX_Y/`.
- Tests: include install/update lifecycle, custom fields, patches, and controller/service behavior.

## Frappe Python Rules

- Do not call `frappe.db.commit()` in controllers, services, or hooks.
- Do not use `frappe.get_all` in user-facing APIs.
- Use `frappe.db.get_list` for permission-aware user-facing reads.
- Use `ignore_permissions=True` only in tests, install, patches, or explicit admin/system contexts.
- Keep controller lifecycle methods deterministic and cheap.
- Do not import mutable service helpers from released patches if their semantics might change.
- Avoid raw SQL unless ORM/query builder is insufficient and the reason is documented.
- Do not mutate submitted/accounting ERPNext documents with low-level writes.

## Patch And Migration Rules

- Add a patch whenever existing sites need metadata, schema, or data moved forward.
- Use `[post_model_sync]` for Custom Field syncs unless pre-schema data conversion is required.
- Patch files must expose `execute()`.
- Patches must be safe to rerun.
- Do not rewrite released patches for new behavior; add a forward-fix patch.
- Git rollback is not enough after schema/data migrations; production requires backup/restore planning.

## Testing Guidelines

Minimum test coverage for feature changes:

- Custom Field existence and metadata.
- Patch first-run and rerun idempotency.
- DocType validation and naming behavior.
- Service normalization and cross-link logic.
- Permission behavior when code syncs between `Mitglied` and ERPNext standard DocTypes.

Use Frappe v16 test classes:

- `frappe.tests.UnitTestCase` for pure helpers.
- `frappe.tests.IntegrationTestCase` for DB-backed DocType behavior.

Always run `bench --site <site> migrate` before `bench --site <site> run-tests --app verein_erp` in CI or release checks.

## Deployment And Release Discipline

Production-style deployment is image-based.

- Build a custom ERPNext image containing ERPNext, Frappe, and `verein_erp`.
- Do not `bench get-app` manually inside production containers as the durable path.
- Keep app containers on the same image tag: backend, frontend, websocket, scheduler, queue-short, queue-long.
- Before migration-bearing deploys, take a backup with files and preserve `site_config.json` including `encryption_key`.
- Rehearse risky updates on staging or a restored production backup.
- After deploy, run `bench migrate` and smoke-test `Customer`, `Mitglied`, link fields, scheduler, and queues.

Normal production update shape:

```bash
podman pull ghcr.io/<owner>/erpnext-verein:<erpnext-version>-<app-version>
# update Quadlet image tags
systemctl --user daemon-reload
systemctl --user restart erpnext-backend.service erpnext-frontend.service erpnext-websocket.service erpnext-scheduler.service erpnext-queue-short.service erpnext-queue-long.service
podman exec -it erpnext-backend bash -lc 'cd /home/frappe/frappe-bench && bench --site <site> migrate'
```

## Security And Data Safety

- Treat member and customer data as sensitive production data.
- Never commit `.env`, backups, private files, database dumps, access tokens, or site secrets.
- `.env`, `.opencode/`, and `.private/` must remain untracked.
- Do not store IBAN/account details in `Mitglied`; use ERPNext's existing payment/account models.
- Cross-DocType sync must enforce permissions before low-level writes.
- Do not document exploit details in branch names, commit messages, PR titles, or tests.

## Common Tasks

Adding a new DocType:

1. Add app DocType files under `verein_erp/verein_erp/doctype/<doctype>/`.
2. Put server-side invariants in the DocType controller.
3. Put reusable logic in `verein_erp/services/`.
4. Add tests for naming, validation, permissions, and links.
5. Add patches only if existing sites need migration/backfill.

Adding a Custom Field to ERPNext:

1. Define it in `verein_erp/custom_fields.py`.
2. Ensure `install.py` calls `sync_custom_fields()`.
3. Add a versioned patch and register it in `patches.txt`.
4. Add or update Custom Field and patch tests.

Changing existing production behavior:

1. Decide whether it is code-only or requires a patch.
2. Preserve old patch semantics.
3. Add tests for fresh install and update path.
4. Document deploy and rollback/restore risk if data/schema changes.

## Review Checklist

Block a change if it introduces:

- ERPNext/Frappe core edits.
- UI-only durable customization.
- Broad Custom Field fixtures.
- Business logic in `hooks.py`.
- Non-idempotent patches.
- `frappe.db.commit()` in normal app code.
- Permission bypass in user-facing APIs or cross-DocType sync.
- Production migration without backup, staging/migrate plan, and smoke tests.

## Current Feature Map

- `Mitglied`: member master data DocType.
- `Customer.mitglied`: app-owned Custom Field linking Customer to Mitglied.
- `Mitglied.customer`: reciprocal Link field.
- `verein_erp.services.mitglied_service`: normalization, date validation, Lastschrift mandate validation, 1:1 relationship enforcement, and permission-aware sync.
