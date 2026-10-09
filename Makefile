.PHONY: help install install-dev test test-integration test-cov lint format clean build check-dist upload-test upload-pypi env-create env-update

# Path to a checkout of vital-agent-container-python (SDK 0.2.0, contract v2) for the
# integration tests. Leave empty if the SDK is installed in the environment.
SDK_PATH ?=

help:
	@echo "Available targets:"
	@echo "  install          - Install the package"
	@echo "  install-dev      - Install the package with development dependencies"
	@echo "  test             - Run the unit tests (integration tests skip without the SDK)"
	@echo "  test-integration - Run the integration tests against the SDK (SDK_PATH=/path/to/sdk)"
	@echo "  test-cov         - Run the tests with coverage"
	@echo "  lint             - Run flake8 and mypy"
	@echo "  format           - Format code (black, isort)"
	@echo "  clean            - Remove build artifacts"
	@echo "  build            - Build sdist and wheel"
	@echo "  check-dist       - Build and check the distribution files"
	@echo "  upload-test      - Upload to test PyPI"
	@echo "  upload-pypi      - Upload to production PyPI"
	@echo "  env-create       - Create the conda development environment"
	@echo "  env-update       - Update the conda development environment"

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

test:
	pytest

test-integration:
	VITAL_AGENT_CONTAINER_SDK_PATH="$(SDK_PATH)" pytest -m integration -v

test-cov:
	pytest --cov=vital_agent_container_client --cov-report=html --cov-report=term

lint:
	flake8 vital_agent_container_client/ test/ examples/
	mypy

format:
	black vital_agent_container_client/ test/ examples/
	isort vital_agent_container_client/ test/ examples/

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .mypy_cache/ .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +

build: clean
	python -m build

check-dist: build
	twine check dist/*

upload-test: check-dist
	@echo "Note: Ensure your ~/.pypirc file is configured with a testpypi repository"
	twine upload --repository testpypi dist/*

upload-pypi: check-dist
	@echo "WARNING: This will upload to production PyPI!"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ] || exit 1
	twine upload dist/*

env-create:
	conda env create -f environment.yml

env-update:
	conda env update -f environment.yml --prune
