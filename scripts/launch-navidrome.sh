#!/bin/sh
# Invoked from S90play; only an explicit SD marker opts into the extension.
# Paths may be overridden only by the host integration test.
if [ "${ND_HOST_TEST:-}" = 1 ]; then
    nd_card=${ND_TEST_CARD:?}
    nd_demo=${ND_TEST_DEMO:?}
    nd_service=${ND_TEST_SERVICE:?}
    nd_plugin=${ND_TEST_PLUGIN:?}
    nd_log=${ND_TEST_LOG:?}
    nd_ca=${ND_TEST_CA:?}
else
    nd_card=/mnt/mmc/navidrome
    nd_demo=/release/bin/demo
    nd_service="$nd_card/bin/navidrome-service"
    nd_plugin="$nd_card/lib/libnavidrome_ui.so"
    nd_log=/tmp/navidrome-service.log
    nd_ca="$nd_card/ca-certificates.crt"
fi
# Diagnostics is a separate opt-in and also works with the extension disabled.
nd_diagnostics=
if [ -f "$nd_card/diagnostics-enable" ] && [ -r "$nd_card/collect-diagnostics.sh" ]; then
    if /bin/sh "$nd_card/collect-diagnostics.sh" "$nd_card" >/dev/null 2>&1; then
        nd_diagnostics="$nd_card/diagnostics/boot.txt"
    fi
fi
nd_record() {
    [ -z "$nd_diagnostics" ] || printf '%s\n' "$1" >> "$nd_diagnostics" 2>/dev/null
    return 0
}
if [ -f "$nd_card/enable" ] && [ -r "$nd_card/config.json" ] &&
   [ -x "$nd_service" ] && [ -r "$nd_plugin" ] && [ -r "$nd_ca" ]; then
    # Keep the TLS trust store and Go runtime limits local to the new service.
    SSL_CERT_FILE="$nd_ca" GOMEMLIMIT=24MiB GOGC=50 GOMAXPROCS=2 \
        "$nd_service" -config "$nd_card/config.json" > "$nd_log" 2>&1 &
    nd_pid=$!
    # Catch immediate exec/config/listen failures before loading the UI extension.
    sleep 1
    if ! kill -0 "$nd_pid" 2>/dev/null; then
        wait "$nd_pid" 2>/dev/null
        nd_record 'backend_initial_state=exited'
        echo 'Navidrome service did not start; using stock application' >> "$nd_log"
        exec "$nd_demo"
    fi
    nd_record 'backend_initial_state=alive'
    # This wrapper remains alive to reap the service if the application exits.
    trap 'kill "$nd_pid" 2>/dev/null; wait "$nd_pid" 2>/dev/null' EXIT
    trap 'exit 0' INT TERM
    LD_PRELOAD="$nd_plugin" "$nd_demo"
    nd_status=$?
    nd_record "extended_application_exit_status=$nd_status"
    kill "$nd_pid" 2>/dev/null
    wait "$nd_pid" 2>/dev/null
    trap - EXIT INT TERM
    if [ "$nd_status" -ne 0 ]; then
        echo 'Extended application exited with an error; restarting stock application' >> "$nd_log"
        exec "$nd_demo"
    fi
    exit 0
else
    nd_record 'extension_state=disabled_or_incomplete'
    exec "$nd_demo"
fi
