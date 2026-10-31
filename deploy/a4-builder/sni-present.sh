#!/bin/sh
# squid external ACL helper (input line: "<sni> -", squid appends %DATA). OK when the client sent an SNI, ERR when absent.
while read -r sni _; do
    case "$sni" in
        "" | "-") echo ERR ;;
        *) echo OK ;;
    esac
done
