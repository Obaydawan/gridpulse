import logging

from gridpulse.utils.logging_setup import get_logger, setup_logging


def test_logging_writes_to_file(tmp_path):
    log_file = tmp_path / "logs" / "test.log"
    setup_logging("INFO", log_file)

    get_logger("gridpulse.test").info("hello gridpulse")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "hello gridpulse" in log_file.read_text(encoding="utf-8")


def test_setup_twice_does_not_duplicate_handlers(tmp_path):
    setup_logging("INFO", tmp_path / "a.log")
    setup_logging("INFO", tmp_path / "a.log")
    assert len(logging.getLogger().handlers) == 2
