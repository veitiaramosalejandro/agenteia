from app.agent.task_status import task_running_status


def test_task_running_status_accepts_raw_and_formatted_codes():
    assert task_running_status(2725, "pt") == "Em curso (2725)"
    assert task_running_status("2725", "pt") == "Em curso (2725)"
    assert task_running_status("Em curso (2725)", "pt") == "Em curso (2725)"
