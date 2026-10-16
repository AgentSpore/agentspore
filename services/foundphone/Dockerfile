FROM caddy:2.11.7@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af
RUN setcap -r /usr/bin/caddy
ENV XDG_CONFIG_HOME=/tmp/caddy-config \
    XDG_DATA_HOME=/tmp/caddy-data \
    GOMEMLIMIT=48MiB \
    GOMAXPROCS=1
COPY Caddyfile /etc/caddy/Caddyfile
COPY index.html /srv/index.html
COPY src/app.js src/render.js src/i18n.js src/presets.js src/styles.css /srv/src/
COPY vendor/qrcode.js vendor/qrcode-generator-LICENSE /srv/vendor/
WORKDIR /srv
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=3s --retries=3 CMD ["curl", "--fail", "--silent", "--show-error", "--max-time", "2", "--output", "/dev/null", "http://127.0.0.1:8080/health"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
