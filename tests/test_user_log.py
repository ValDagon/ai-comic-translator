from service.user_log import line_for_job_db


def test_line_for_job_db_drops_mit_noise():
    assert line_for_job_db("INFO: loading model") is None
    assert line_for_job_db("Запускаю: python -m manga_translator") is None


def test_line_for_job_db_keeps_pipeline():
    assert line_for_job_db("Найдено 2 страниц, 3 облачков с текстом") is not None
