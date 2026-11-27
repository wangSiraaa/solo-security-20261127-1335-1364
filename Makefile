.PHONY: test demo plans clean

test:
	python3 -m unittest discover -s tests -v

demo:
	./sealctl demo --state demo-case

plans:
	./sealctl plans --all

clean:
	rm -rf .seal-case demo-case
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -prune -exec rm -rf {} +
