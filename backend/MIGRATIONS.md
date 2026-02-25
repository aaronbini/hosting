# Database Migrations

This project uses [Alembic](https://alembic.sqlalchemy.org/) for schema migrations against a PostgreSQL database.

All commands below should be run from the `backend/` directory.

---

## Prerequisites

Ensure `DATABASE_URL` is set in `backend/.env`:

```
DATABASE_URL=postgresql+asyncpg://<user>@localhost/<dbname>
```

---

## Common commands

### Apply all pending migrations
```bash
uv run alembic upgrade head
```
Run this after cloning the repo or pulling changes that include new migration files. Safe to run repeatedly — already-applied migrations are skipped.

### Check current DB version
```bash
uv run alembic current
```

### View migration history
```bash
uv run alembic history --verbose
```

---

## Making a schema change

1. Edit the SQLAlchemy models in `app/db/models.py`
2. Generate a migration:
   ```bash
   uv run alembic revision --autogenerate -m "short description of change"
   ```
3. Review the generated file in `migrations/versions/` — autogenerate is good but not perfect, especially for renames or complex constraints
4. Apply it:
   ```bash
   uv run alembic upgrade head
   ```

---

## Rolling back

```bash
# Roll back the last applied migration
uv run alembic downgrade -1

# Roll back to a specific revision (use the hash from `alembic history`)
uv run alembic downgrade <revision_id>
```

---

## Production deploys

Run migrations as part of your deploy step, **before** starting the server:

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app ...
```

This is safe to run on every deploy — already-applied migrations are no-ops.

---

## How it works

- `alembic.ini` — Alembic config; the `sqlalchemy.url` value is overridden at runtime by `DATABASE_URL` from the environment
- `migrations/env.py` — configured for async SQLAlchemy (`asyncpg`); imports `Base` and all models so autogenerate can detect schema drift
- `migrations/versions/` — one file per migration, tracked in the `alembic_version` table in the database
