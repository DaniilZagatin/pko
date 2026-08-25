.PHONY: test test-verbose test-report fixture clean

PYTHON ?= python3
export PYTHONPATH := backend

# Единственная команда проверки — она же документирована в README и предназначена
# для CI. Тестовый репозиторий создаётся автоматически, отдельный шаг не нужен.
test:
	$(PYTHON) -m unittest discover -s tests

test-verbose:
	$(PYTHON) -m unittest discover -s tests -v

# Тот же прогон, но с записью результата в `reports/`: команда, код возврата,
# исход каждого теста. Нужен там, где «у меня всё прошло» не является ответом —
# CI, приёмка, внешний контроль.
test-report:
	$(PYTHON) tests/run_tests.py

fixture:
	bash tests/make_fixture.sh

clean:
	rm -rf pko-progress-out reports build dist backend/*.egg-info tests/fixtures/mini_repo
	find backend tests -name __pycache__ -type d -exec rm -rf {} +
