# Administration

## The Admin page

The first account created on an instance is its **admin**. Admins get an extra **Settings → Admin** page with:

- **Users** — every account with its last sign-in and item count. Deactivate or reactivate an account, make another account an admin (a useful spare key), or reset a password.
- **Registration** — whether sign-up is open. This is controlled by the `REGISTRATION` environment variable, not from the page.
- **Caches** — image cache usage against its cap, plus the purge-image-cache and clear-search-cache actions.
- **Housekeeping** — how many unused catalog items the weekly prune would remove, with a button to run it now.
- **Backups** — when the last backup ran and how each step went, how many copies are on the server, and a "Back up now" button. See [Backups](#backups).
- **Scheduled tasks** — last run, next run and last result of each background task, with "Run now".
- **Health** — Redis, the background worker, database size and version.

!!! tip "Close registration after creating your account"
    Registration is open by default and the first account becomes the admin. Create your account straight after installing, then set `REGISTRATION=False` (see [env-variables](env-variables.md)) unless you want anyone who can reach the site to sign up.

### Forgotten passwords

MyTrackz does not send email, so there is no "forgot password" link.

- **Another user is locked out**: on the Admin page choose **Reset password** next to their account. You are shown a temporary password once; pass it on. They are asked to choose a new password as soon as they sign in with it.
- **The admin is locked out**: reset it from the server, where `<username>` is the admin's username:

      ```bash
      docker compose exec yamtrack python manage.py changepassword <username>
      ```

- If single sign-on is configured, that login path is unaffected by a forgotten local password.

To make an account an admin from the server (for example if the only admin account was deactivated):

```bash
docker compose exec yamtrack python manage.py shell -c "from users.models import User; User.objects.filter(username='<username>').update(is_staff=True, is_active=True)"
```

## Backups

MyTrackz backs its SQLite database up by itself every day at 03:00 (`BACKUP_HOUR`). Each run:

1. **Copies the database** to the backup folder with SQLite's online backup API, so the copy is consistent even while the app is in use. The copy is checked for integrity before it is kept.
2. **Exports every account's library as CSV** into one zip, so a single user can be restored through **Settings → Import** without replacing the whole database.
3. **Deletes copies older than a week** (`BACKUP_RETENTION_DAYS`, 7 days by default).

A run produces two files named after the time it started, in UTC:

```text
backups/mytrackz-20261111T030000Z.sqlite3.gz
backups/mytrackz-20261111T030000Z-exports.zip
```

If a step fails, the steps before it are kept. Old copies are only deleted after a new one was written, so a run of failures cannot age out the last good backup. Only files with the names above are ever deleted; anything else in the folder is left alone.

!!! warning "These copies live on the same server"
    The backups protect against a bad update, a mistaken deletion or a damaged database. They do not protect against losing the server or its disk. Include the `backups` folder in whatever backs up the server itself, or copy it elsewhere.

!!! warning "Backup files are sensitive"
    A backup is the whole database: accounts, password hashes and the tokens used for imports. Files are written readable by the app user only and are not encrypted, so keep the folder private.

The cached images in `media/` are not backed up. They are downloaded again on demand, and custom posters are stored as URLs in the database. Instances running on PostgreSQL are not backed up by this feature; use `pg_dump` there.

The settings are listed under [env-variables](env-variables.md#backups). The **Settings → Admin** page shows the last run and has a **Back up now** button; you can also run one from the server:

```bash
docker compose exec -u abc yamtrack python manage.py shell -c "from app.tasks import back_up_database; print(back_up_database())"
```

### Storing backups outside the container

The backup folder must be a mounted volume, or the copies disappear when the container is rebuilt. The example `docker-compose.yml` mounts `./backups`:

```yaml
volumes:
  - ./backups:/yamtrack/backups
```

### Notifications

Set `BACKUP_NOTIFICATION_URLS` to one or more [Apprise](https://github.com/caronc/apprise/wiki) URLs to get a message after every run. A Discord webhook URL (`https://discord.com/api/webhooks/<webhook_id>/<webhook_token>`) can be used as it is:

```yaml
environment:
  - BACKUP_NOTIFICATION_URLS=https://discord.com/api/webhooks/<webhook_id>/<webhook_token>
  - BACKUP_INSTANCE_NAME=prod
```

A completed backup reports what was backed up, its size, how long it took and how many copies are kept. A failed one names the step that failed and the error. Set `BACKUP_NOTIFY_ON_SUCCESS=False` to hear about failures only. The webhook URL is a secret, so keep it in your override file or a Docker secret, not in a shared compose file. A notification that cannot be delivered never fails the backup.

### Restoring

Restoring replaces the current database with a backup. Everything tracked since that backup was taken is lost, so the steps below keep the current database aside first. From the folder holding `docker-compose.yml`:

```bash
docker compose stop yamtrack
cp db/db.sqlite3 db/db.sqlite3.before-restore
gunzip -c backups/mytrackz-20261111T030000Z.sqlite3.gz > db/db.sqlite3
rm -f db/db.sqlite3-wal db/db.sqlite3-shm
docker compose start yamtrack
```

Removing the `-wal` and `-shm` files matters: they belong to the database that was replaced, and leaving them corrupts the restored one. If your `db` folder is mounted from somewhere else, use that path instead of `db/`.

Then sign in and check a few recent entries. Once you are satisfied, delete `db/db.sqlite3.before-restore`.

**One user's library**: unzip `mytrackz-…-exports.zip` and import that user's CSV under **Settings → Import → Yamtrack CSV**, signed in as them.

## The Django admin interface

The Django admin is a separate, lower-level tool for editing database records directly. It is off by default. To access it, follow these steps:

## Enabling the Admin Interface

1. **Set Environment Variable**: You need to enable the admin interface by setting the environment variable `ADMIN_ENABLED` to `True`. This can be done in your environment configuration file (e.g., `.env` file) or directly in your `docker-compose.yml`.

      ```bash
      ADMIN_ENABLED=True
      ```

      For related settings, see [env-variables](env-variables.md).

2. **Access the Admin Page**: Once enabled, you can access the admin interface at the following URL:

      ```bash
      https://domain.com/admin/
      ```

## Logging In

To log in to the admin interface, you will need an admin account. You can either change an existing user to have admin privileges or create a new user with those roles.

### Changing an Existing User to Admin

1. **Open Django Shell**: If you are using Docker, you can access the Django shell by running the following command in your terminal:

      ```bash
      docker exec -it yamtrack python manage.py shell
      ```

2. **Set Admin Privileges**: Find the user you want to promote to admin. Replace `username` with the actual username of the user.

      ```python
      User.objects.filter(username='username').update(is_staff=True, is_superuser=True)
      ```

### Creating a New Admin User

1. **Open Django Shell**: As mentioned above, access the Django shell using Docker:

      ```bash
      docker exec -it yamtrack python manage.py shell
      ```

2. **Create a New User**: Use the following code to create a new user. Replace `new_username`, `new_password`, and `new_email` with the desired values.

      ```python
      User.objects.create_user(username='new_username', password='new_password', is_staff=True, is_superuser=True)
      ```

3. **Log In**: After creating the new user, you can log in to the admin interface using the new credentials.

### Admin Interface Overview

After logging in, you will see various tables representing the different models in your database:

Here, you can view and edit data. For example, to edit an item image:

![image](https://github.com/user-attachments/assets/8177cf5b-2f2b-4e51-b53e-003dd38c7503)

1. Click on the **Items** entry.
2. You will see a list of items. Click on the specific item you want to change.
3. In the item detail view, you can update the image URL in the corresponding field.
