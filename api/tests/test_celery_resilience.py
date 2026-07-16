from api.tasks.celery_app import celery_app


def test_celery_redelivers_worker_loss_without_killing_healthy_long_jobs() -> None:
    conf = celery_app.conf

    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options["visibility_timeout"] >= 12 * 60 * 60
    assert conf.task_time_limit is None
    assert conf.task_soft_time_limit is None
