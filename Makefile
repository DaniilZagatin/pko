.PHONY: test test-verbose test-report fixture patch bundle coverage-doc clean

PYTHON ?= python3
export PYTHONPATH := src

# Единственная команда проверки — она же документирована в README и предназначена
# для CI. Тестовый репозиторий создаётся автоматически, отдельный шаг не нужен.
test:
	$(PYTHON) -m unittest discover -s tests

test-verbose:
	$(PYTHON) -m unittest discover -s tests -v

# Тот же прогон, но с записью результата в `reports/`: команда, код возврата,
# исход каждого теста. Нужен там, где «у меня всё прошло» не является ответом —
# CI, приёмка, внешний контроль. Формат машинного отчёта тот же, который PKO
# требует от анализируемых систем и умеет читать сам.
test-report:
	$(PYTHON) tests/run_tests.py

fixture:
	bash tests/make_fixture.sh

# Документ о покрытии стандарта собирается из каталога требований: вести его
# руками значит немедленно разойтись с кодом.
coverage-doc:
	$(PYTHON) -m pko.standard.coverage_doc --write

# Два способа переноса на другой компьютер: см. transfer/README.md.
# `patch` — для git-дерева на том же коммите, `bundle` — каталог файлов
# для копирования поверх установленной копии.
patch:
	$(PYTHON) transfer/make_patch.py

bundle:
	$(PYTHON) transfer/make_bundle.py

clean:
	rm -rf pko-out reports bench/runs build dist src/*.egg-info transfer/out transfer/bundle \
		tests/fixtures/mini_repo tests/fixtures/multistack_repo
	find src tests -name __pycache__ -type d -exec rm -rf {} +
