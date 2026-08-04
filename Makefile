.PHONY: test test-verbose fixture clean

PYTHON ?= python3
export PYTHONPATH := src

# Единственная команда проверки — она же документирована в README и предназначена
# для CI. Тестовый репозиторий создаётся автоматически, отдельный шаг не нужен.
test:
	$(PYTHON) -m unittest discover -s tests

test-verbose:
	$(PYTHON) -m unittest discover -s tests -v

fixture:
	bash tests/make_fixture.sh

clean:
	rm -rf pko-out tests/fixtures/mini_repo
	find src tests -name __pycache__ -type d -exec rm -rf {} +
