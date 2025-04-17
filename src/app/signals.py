import logging

from celery import states
from celery.signals import before_task_publish, worker_init
from django.db.backends.signals import connection_created
from django.dispatch import receiver
from django_celery_results.models import TaskResult

logger = logging.getLogger(__name__)


@receiver(connection_created)
def setup_sqlite_pragmas(sender, connection, **kwargs):  # noqa: ARG001
    """Set up SQLite pragmas for WAL mode and busy timeout on connection creation."""
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=wal;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()


@before_task_publish.connect
def create_task_result_on_publish(sender=None, headers=None, body=None, **kwargs):  # noqa: ARG001
    """Create a TaskResult object with PENDING status on task publish.

    https://github.com/celery/django-celery-results/issues/286#issuecomment-1279161047
    """
    if "task" not in headers:
        return

    TaskResult.objects.store_result(
        content_type="application/json",
        content_encoding="utf-8",
        task_id=headers["id"],
        result=None,
        status=states.PENDING,
        task_name=headers["task"],
        task_args=headers.get("argsrepr", ""),
        task_kwargs=headers.get("kwargsrepr", ""),
    )


@worker_init.connect(weak=False)
def cleanup_periodic_tasks(*_, **__):
    """Clean up periodic tasks by disabling those not registered."""
    from celery import current_app as app
    from django_celery_beat.models import PeriodicTask

    registered_tasks = [item.get("task") for item in app.conf.beat_schedule.values()]
    for periodic_task in PeriodicTask.objects.exclude(
        task="celery.backend_cleanup",
    ).filter(enabled=True):
        if periodic_task.task not in registered_tasks:
            periodic_task.enabled = False
            periodic_task.save()
            logger.info("Disabled periodic task: %s", periodic_task.task)
