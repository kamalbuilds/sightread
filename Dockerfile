FROM python:3.12-slim

# ffmpeg is not optional here: every number this product reports comes from
# silencedetect, scdet and ffprobe, and the conform loop re-measures rendered
# audio from disk. Without it the page can still serve completed runs, but
# nothing new can be measured.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The image asserts its own dependencies at build time rather than at first
# request. ffmpeg missing from a built image is a broken image, and the gates in
# tests/conftest.py turn that from a quiet skip into a failure when these are set,
# so a build that would serve unmeasurable pages cannot be pushed.
ENV SIGHTREAD_REQUIRE_FFMPEG=1 \
    SIGHTREAD_REQUIRE_TAKES=1
RUN python -m pytest tests/ -q \
      --deselect tests/test_live_vertex.py \
    && python -c "import agent.graph, web.server; print('import graph and server: ok')"

# The source film is deliberately NOT in the image. It is a 371MB public-domain
# master and the page reads entirely from out/, which holds the completed reports
# and every accepted and rejected take. The hosted copy therefore serves a real
# finished run; the live conform path reports plainly that the media is absent
# and gives the command to reproduce it locally.
ENV PORT=8080 \
    GOOGLE_GENAI_USE_VERTEXAI=true \
    SIGHTREAD_MEDIA=/tmp/sightread-media

EXPOSE 8080
CMD ["sh", "-c", "uvicorn web.server:app --host 0.0.0.0 --port ${PORT}"]
