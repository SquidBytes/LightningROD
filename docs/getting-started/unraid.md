# Unraid Setup

Run LightningROD on Unraid with Docker Compose Manager.

!!! note
    This guide assumes you are using the Unraid Docker Compose Manager plugin and want LightningROD deployed as a stack.

## Prerequisites

- Unraid server with Docker enabled
- [Docker Compose Manager plugin](https://forums.unraid.net/topic/114415-plugin-docker-compose-manager/)

## 1. Clone the project into `appdata`

Open a terminal on your Unraid server (SSH, local terminal, or terminal add-on), then run:

```bash title="" 
git clone https://github.com/SquidBytes/LightningROD.git /mnt/user/appdata/LightningROD/
```

## 2. Create a new stack

1. Open the **Docker** page in the Unraid web UI.
2. Scroll to the Docker Compose Manager section.
3. Click **Add New Stack**.
4. Name it (for example, `LightningROD`).

## 3. Set the stack ENV file path

1. Click the gear icon for the stack.
2. Go to **Edit Stack** -> **Stack Settings**.
3. Set **ENV File Path** to:

```text
/mnt/user/appdata/LightningROD/.env
```

## 4. Configure the `.env` values

1. Click the gear icon for the stack.
2. Go to **Edit Stack** -> **Env File**.
3. Set at least:
    - `POSTGRES_USER`
    - `POSTGRES_PASSWORD`
4. Save the file.

!!! warning
    Use a strong password for `POSTGRES_PASSWORD` and store it in your password manager.

## 5. Update the Compose File

1. Click the gear icon for the stack.
2. Go to **Edit Stack** -> **Compose File**.
3. Use this Compose configuration:

```yaml title="docker-compose.yml"
services:
  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data
    env_file: .env
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
    restart: unless-stopped

  web:
    image: ghcr.io/squidbytes/lightningrod-web:latest
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      # Required: the app reads DATABASE_URL directly and will not start without it.
      # The asyncpg driver scheme is required (plain postgresql:// will fail with
      # a missing psycopg2 error). The ${POSTGRES_*} values are pulled from .env.
      - DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db/${POSTGRES_DB}
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped

volumes:
  pgdata:
```

=== "Default Port Mapping"

    Use:

    ```yaml
    ports:
      - "8000:8000"
    ```

=== "Custom External Port"

    If port `8000` is already in use on your Unraid host, change only the external side:

    ```yaml
    ports:
      - "8090:8000"
    ```

    Format is `EXTERNAL:INTERNAL`.

## 6. Optional UI labels and Web UI link

You can set icons and quick links in **UI Labels** for each service.

Example icon URLs/paths:

```text
# db icon
https://www.postgresql.org/media/img/about/press/elephant.png

# web icon (if you saved a local PNG on your server)
/mnt/user/appdata/LightningROD/logo.png
```

For the web service Web UI field, use:

```text
http://[IP]:8000
```

If you changed the external port, replace `8000` with that port.

## 7. Start the stack

Click **Compose Up**.

## Verify

- Open `http://[IP]:8000` (or your mapped external port)
- Confirm the `db` and `web` services are running in Docker Compose Manager

You can now continue with [Configuration](configuration.md) and [Data Import](data-import.md).

## Alternative: Standalone (SQLite)

If you'd rather run a single container with an embedded SQLite database instead of the two-container Postgres stack, use this Compose file at step 5 in place of the one above. The `POSTGRES_*` values in `.env` are unused in this mode -- only `APP_PORT`, `APP_DEBUG`, and `DEMO_MODE` apply.

```yaml title="docker-compose.yml (standalone)"
services:
  lightningrod:
    image: ghcr.io/squidbytes/lightningrod-web:latest
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - DATABASE_URL=sqlite+aiosqlite:////data/lightningrod.db
    volumes:
      - /mnt/user/appdata/LightningROD/data:/data
    restart: unless-stopped
```

The bind mount puts the SQLite file at `/mnt/user/appdata/LightningROD/data/lightningrod.db` on your array so it's backed up alongside the rest of your appdata. Everything else (steps 1-4, 6-7) stays the same.
