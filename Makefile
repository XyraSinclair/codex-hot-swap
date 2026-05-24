.PHONY: test check

test:
	./tests/run-tests.sh

check: test
	git diff --check
