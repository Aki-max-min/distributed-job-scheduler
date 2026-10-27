from datetime import datetime
from app.models import Job, Queue, Project, Organization, Worker
from app.worker_service import CLAIM_JOB_SQL


def _create_worker(db_session, hostname):
    worker = Worker(hostname=hostname, status="idle")
    db_session.add(worker)
    db_session.commit()
    return worker.id


def _create_pending_job(db_session):
    org = Organization(name="Org")
    db_session.add(org)
    db_session.flush()

    project = Project(org_id=org.id, name="Project")
    db_session.add(project)
    db_session.flush()

    queue = Queue(project_id=project.id, name="q1")
    db_session.add(queue)
    db_session.flush()

    job = Job(queue_id=queue.id, job_type="immediate", status="pending", run_at=datetime.utcnow())
    db_session.add(job)
    db_session.commit()
    return job.id


def test_claim_marks_job_running_and_sets_claimed_by(db_session):
    job_id = _create_pending_job(db_session)
    worker_a = _create_worker(db_session, "worker-a")

    result = db_session.execute(CLAIM_JOB_SQL, {"worker_id": worker_a})
    row = result.fetchone()
    db_session.commit()

    assert row is not None
    assert str(row.id) == str(job_id)

    db_session.expire_all()
    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job.status == "running"
    assert job.claimed_by == worker_a
    assert job.attempt_count == 1


def test_claim_does_not_return_already_running_job(db_session):
    _create_pending_job(db_session)
    worker_a = _create_worker(db_session, "worker-a")
    worker_b = _create_worker(db_session, "worker-b")

    # First claim succeeds
    db_session.execute(CLAIM_JOB_SQL, {"worker_id": worker_a})
    db_session.commit()

    # Second claim attempt should find nothing — job is no longer pending
    result = db_session.execute(CLAIM_JOB_SQL, {"worker_id": worker_b})
    row = result.fetchone()
    db_session.commit()

    assert row is None