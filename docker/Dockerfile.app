FROM python:3.13.14-slim

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT="/usr/local/" \
    UV_COMPILE_BYTECODE=1 \
    PYTHONPATH=/app/src
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.12.5 \
    && uv sync --frozen --only-group client
COPY src/client/ ./src/client/
COPY src/schema/ ./src/schema/
COPY src/voice/ ./src/voice/
COPY src/shipping_ui.py src/streamlit_app.py ./src/
CMD ["streamlit", "run", "src/streamlit_app.py", "--server.address=0.0.0.0"]
