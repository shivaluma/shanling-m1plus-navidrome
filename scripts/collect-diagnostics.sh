#!/bin/sh
# Opt-in, bounded hardware snapshot. Never read config, environment or app logs.
nd_card=$1
[ -n "$nd_card" ] && [ -d "$nd_card" ] && [ -f "$nd_card/diagnostics-enable" ] || exit 1
nd_proc=/proc
if [ "${ND_HOST_TEST:-}" = 1 ]; then
    nd_proc=${ND_TEST_PROC:?}
fi
umask 077
nd_dir="$nd_card/diagnostics"
[ ! -L "$nd_dir" ] || exit 1
mkdir -p "$nd_dir" || exit 1
nd_tmp="$nd_dir/.boot.$$"
trap 'rm -f "$nd_tmp"' EXIT
{
    echo 'format=navidrome-m1plus-diagnostics-v1'
    echo 'scope=boot snapshot, not audio or GUI verification'
    printf 'utc_time='; date -u '+%Y-%m-%dT%H:%M:%SZ'
    echo '[kernel_release]'
    sed -n '1p' "$nd_proc/sys/kernel/osrelease" 2>/dev/null
    echo '[cpu_capabilities]'
    awk -F: 'NR <= 120 && tolower($1) ~ /^(system type|machine|processor|cpu model|isa|ases implemented|fpu|tlb entries|byteorder|bogomips)[ \t]*$/ {print}' "$nd_proc/cpuinfo" 2>/dev/null
    echo '[memory]'
    awk -F: '$1 ~ /^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree)$/ {print}' "$nd_proc/meminfo" 2>/dev/null
    echo '[mtd_partitions]'
    sed -n '1,64p' "$nd_proc/mtd" 2>/dev/null
    echo '[nan_boot_override]'
    nd_nan=not_explicitly_set
    for nd_word in $(cat "$nd_proc/cmdline" 2>/dev/null); do
        case "$nd_word" in
            ieee754=strict|ieee754=legacy|ieee754=2008|ieee754=relaxed) nd_nan=$nd_word ;;
        esac
    done
    echo "$nd_nan"
    echo '[sd_mount]'
    awk '$2 == "/mnt/mmc" {print "filesystem=" $3; if (index("," $4 ",", ",noexec,")) print "execution=noexec"; else print "execution=not_disabled_by_mount_flag"; if (index("," $4 ",", ",ro,")) print "writable=read_only"; else print "writable=read_write"}' "$nd_proc/mounts" 2>/dev/null
    echo '[sd_space_kib]'
    # Offline downloads need free space on the card; sizes only, no file names.
    df -k "$nd_card" 2>/dev/null | awk 'NR == 2 {print "total=" $2; print "available=" $4}'
} > "$nd_tmp" || exit 1
mv -f "$nd_tmp" "$nd_dir/boot.txt" || exit 1
exit 0
