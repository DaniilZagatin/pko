.PHONY: test test-verbose fixture patch bundle clean

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

# Два способа переноса на другой компьютер: см. transfer/README.md.
# `patch` — для git-дерева на том же коммите, `bundle` — каталог файлов
# для копирования поверх установленной копии.
patch:
	$(PYTHON) transfer/make_patch.py

bundle:
	$(PYTHON) transfer/make_bundle.py

clean:
	rm -rf pko-out bench/runs build dist src/*.egg-info transfer/out transfer/bundle \
		tests/fixtures/mini_repo tests/fixtures/multistack_repo
	find src tests -name __pycache__ -type d -exec rm -rf {} +
