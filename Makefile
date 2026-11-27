.PHONY: all demo build online review mutation verify clean

all: demo

demo:
	./run_demo.sh

build:
	./coop build

online:
	python3 scripts/online_flow.py

review:
	python3 scripts/external_review.py
	python3 scripts/mutation_sequence.py

mutation:
	python3 scripts/mutation_sequence.py

verify:
	@test -n "$(BUNDLE)" || (echo "usage: make verify BUNDLE=path [ORIGINAL=path]"; exit 2)
	./verify_independent.py "$(BUNDLE)" $(if $(ORIGINAL),--original "$(ORIGINAL)",)

clean:
	rm -rf cabinet/private/keys cabinet/private/originals cabinet/private/witness-log
	rm -rf deliverables/bundles/* deliverables/reader-originals/* deliverables/trust/*
