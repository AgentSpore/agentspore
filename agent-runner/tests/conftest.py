import os

# Set mandatory env vars before any test module is imported so that
# RunnerSettings (pydantic-settings) can be instantiated at collection time.
os.environ.setdefault("RUNNER_KEY", "test-runner-key")
