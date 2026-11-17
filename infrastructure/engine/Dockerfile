FROM golang:1.27.1-trixie AS builder
WORKDIR /build
RUN git clone --depth 1 --branch v1.18.1 https://github.com/gosom/google-maps-scraper.git engine \
    && test "$(git -C engine rev-parse HEAD)" = 549e4b5e61c7103685ef8392f246ebdba783ed03 \
    && git clone --depth 1 https://github.com/gosom/scrapemate.git scrapemate \
    && test "$(git -C scrapemate rev-parse HEAD)" = 859d15f56ba5ed3851587edc305fab6ee956cc71
COPY browser-close.patch /build/browser-close.patch
# The dependency patch is reviewed locally; no unmerged fork is installed implicitly.
RUN cd scrapemate && git apply --check /build/browser-close.patch && git apply /build/browser-close.patch
RUN cd scrapemate && go test -race -count=1 -timeout=120s ./adapters/fetchers/jshttp -run 'Test(BrowserCloseAbandonsHungContextAndStillClosesBrowser|PageSlotPoolCloseAbandonsHungContext)$'
RUN cd engine && go mod edit -replace github.com/gosom/scrapemate=/build/scrapemate \
    && go mod download \
    && go test -count=1 -timeout=120s ./web/... \
    && CGO_ENABLED=0 go build -ldflags='-w -s' -o /build/google-maps-scraper

# Run the same regression against the original production code: it must reproduce both hangs.
FROM builder AS baseline
RUN cd /build/scrapemate \
    && git restore adapters/fetchers/jshttp/jshttp.go adapters/fetchers/jshttp/page_slot_pool.go \
    && printf 'package jshttp\nimport "time"\nvar browserCloseTimeout = time.Second\n' > adapters/fetchers/jshttp/baseline_timeout_test.go \
    && if go test -race -count=1 -timeout=30s ./adapters/fetchers/jshttp -run 'Test(BrowserCloseAbandonsHungContextAndStillClosesBrowser|PageSlotPoolCloseAbandonsHungContext)$' > /build/baseline.log 2>&1; then cat /build/baseline.log; exit 1; fi \
    && cat /build/baseline.log \
    && grep -q 'browser.Close blocked on a hung context close' /build/baseline.log \
    && grep -q 'pageSlotPool.close blocked on a hung context close' /build/baseline.log

# Preserve the exact runtime/browser versions and all engine defaults from the installed image.
FROM gosom/google-maps-scraper@sha256:e205c02913c5a69c16fc2094b8e5b194a2f655166d2c1126d50548d216e6b1b2
LABEL org.opencontainers.image.source="https://github.com/gosom/google-maps-scraper" \
      org.opencontainers.image.version="v1.18.1-closefix1" \
      ai.google-maps-scraper-kit.patch="gosom/scrapemate#31@436425d8f71ee3426ef789b24917e1b8487b8db7"
COPY --from=builder /build/google-maps-scraper /usr/bin/google-maps-scraper
COPY --from=builder /build/engine/LICENSE /usr/share/licenses/google-maps-scraper/LICENSE
COPY --from=builder /build/scrapemate/LICENSE /usr/share/licenses/scrapemate/LICENSE
COPY --from=baseline /build/baseline.log /usr/share/google-maps-scraper-kit/engine-close-baseline.log
