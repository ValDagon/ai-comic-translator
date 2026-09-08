from service.db import JobRow
from service.job_progress import (
    estimate_job_progress,
    page_progress,
    project_progress,
    snap_percent,
)


def _job(**kwargs) -> JobRow:
    base = dict(
        id="j1",
        project_id="p1",
        kind="full",
        status="running",
        log="",
        error=None,
        created_at="2026-01-01T00:00:00",
        started_at="2026-01-01T00:00:01",
        finished_at=None,
        options_json="{}",
    )
    base.update(kwargs)
    return JobRow(**base)


def test_snap_percent_steps_of_five():
    assert snap_percent(0) == 0
    assert snap_percent(2) == 0
    assert snap_percent(3) == 5
    assert snap_percent(12) == 10
    assert snap_percent(13) == 15
    assert snap_percent(97) == 95
    assert snap_percent(98) == 100
    assert snap_percent(100) == 100


def test_queued_and_done():
    assert estimate_job_progress(_job(status="queued")).percent == 0
    assert estimate_job_progress(_job(status="done")).percent == 100
    assert estimate_job_progress(_job(status="done")).state == "done"


def test_full_extract_stages_snap_to_five():
    start = estimate_job_progress(_job(log="Запуск…"))
    assert start.percent % 5 == 0
    assert 5 <= start.percent <= 15

    extract = estimate_job_progress(
        _job(log="Запуск…\nИзвлечение текста и очистка облачков (может занять несколько минут)…")
    )
    assert extract.percent == 25

    parse = estimate_job_progress(
        _job(log="Извлечение завершено.\nСчитываю текст с страниц…")
    )
    assert parse.percent == 40

    batch = estimate_job_progress(
        _job(
            log=(
                "Отправляю текст на перевод…\n"
                "  пачка 2/4: страницы 31–60 из 100"
            )
        )
    )
    assert batch.percent % 5 == 0
    assert 45 <= batch.percent <= 70

    render = estimate_job_progress(
        _job(
            log=(
                "Вставляю перевод на страницы…\n"
                "Готова страница: 01.png\n"
                "Готова страница: 02.png"
            )
        ),
        input_count=10,
    )
    assert render.percent % 5 == 0
    assert 75 <= render.percent <= 95


def test_failed_keeps_estimate():
    p = estimate_job_progress(
        _job(status="failed", log="Считываю текст с страниц…", error="boom")
    )
    assert p.state == "failed"
    assert p.percent == 40


def test_project_progress_picks_active_job():
    jobs = [
        _job(id="old", status="done", created_at="2026-01-01T00:00:00"),
        _job(
            id="active",
            status="running",
            log="Запуск…",
            created_at="2026-01-02T00:00:00",
        ),
    ]
    # list_jobs returns newest first
    jobs = list(reversed(jobs))
    p = project_progress(jobs, input_count=10, out_count=0)
    assert p.state == "running"
    assert p.percent > 0


def test_project_progress_idle_empty():
    p = project_progress([], input_count=0, out_count=0)
    assert p.percent == 0
    assert p.state == "idle"


def test_page_progress_during_render():
    current, total = page_progress(
        _job(
            status="running",
            kind="render",
            log="Вставляю перевод на страницы…\nГотова страница: 01.png\nГотова страница: 02.png",
        ),
        input_count=10,
        out_count=2,
    )
    assert current == 2
    assert total == 10


def test_estimate_job_progress_includes_page_fields():
    p = estimate_job_progress(
        _job(
            status="running",
            kind="full",
            log="Вставляю перевод на страницы…\nГотова страница: 01.png",
        ),
        input_count=5,
        out_count=1,
    )
    assert p.page_current == 1
    assert p.page_total == 5
