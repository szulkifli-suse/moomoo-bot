# Runtime only. The OpenD binary is NOT baked in: the entrypoint downloads it from moomoo's
# official server onto the pod's volume on first start, so no moomoo software is redistributed.
FROM ubuntu:22.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home --home-dir /home/app app
COPY docker/opend-entrypoint.sh /usr/local/bin/opend-entrypoint
RUN chmod 755 /usr/local/bin/opend-entrypoint
USER 10001
ENTRYPOINT ["/usr/local/bin/opend-entrypoint"]
