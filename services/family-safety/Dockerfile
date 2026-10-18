FROM caddy:2.11.7@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af
RUN setcap -r /usr/bin/caddy
ENV XDG_CONFIG_HOME=/tmp/caddy-config XDG_DATA_HOME=/tmp/caddy-data GOMEMLIMIT=48MiB GOMAXPROCS=1
COPY Caddyfile /etc/caddy/Caddyfile
COPY index.html /srv/index.html
COPY src/app.js src/styles.css /srv/src/
COPY content/curriculum.json \
    content/en.json \
    content/ru.json \
    content/es.json \
    content/pt.json \
    content/fr.json \
    content/de.json \
    content/it.json \
    content/nl.json \
    content/pl.json \
    content/uk.json \
    content/cs.json \
    content/sk.json \
    content/ro.json \
    content/hu.json \
    content/el.json \
    content/tr.json \
    content/ar.json \
    content/he.json \
    content/fa.json \
    content/hi.json \
    content/bn.json \
    content/ur.json \
    content/id.json \
    content/ms.json \
    content/vi.json \
    content/th.json \
    content/ko.json \
    content/ja.json \
    content/zh-Hans.json \
    content/sw.json \
    content/tl.json \
    /srv/content/
WORKDIR /srv
USER 65532:65532
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=3s --retries=3 CMD ["curl", "--fail", "--silent", "--show-error", "--max-time", "2", "--output", "/dev/null", "http://127.0.0.1:8080/health"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
