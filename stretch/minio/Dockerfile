# The MinIO community image disappeared from public registries in 2026.
# Build the last community tag from its official source for this local lab.
FROM golang:1.24.2-bookworm AS build
RUN GOBIN=/out go install github.com/minio/minio@RELEASE.2025-09-07T16-13-09Z

FROM debian:bookworm-slim
RUN useradd --system --uid 10001 --create-home minio && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
