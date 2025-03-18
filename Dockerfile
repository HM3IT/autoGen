# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Set environment variables for uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Set the working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./
 
RUN uv add jupyter

# Install project dependencies without installing the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application code
COPY . .

# Install the project into the virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.12-slim-bookworm

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH" \
    TOKEN="UNSET"

# Set the working directory
WORKDIR /app

# Copy the application from the builder stage
COPY --from=builder /app /app

# Expose the port
EXPOSE 8888

# Define the entrypoint command
CMD ["python", "-m", "jupyter", "kernelgateway", \
    "--KernelGatewayApp.ip=0.0.0.0", \
    "--KernelGatewayApp.port=8888", \
    "--KernelGatewayApp.auth_token=${TOKEN}", \
    "--JupyterApp.answer_yes=true", \
    "--JupyterWebsocketPersonality.list_kernels=true"]
