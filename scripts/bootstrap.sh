#!/usr/bin/env bash
# Rosetta bootstrap -- prepare a DEDICATED, QUIESCED YottaDB verification container.
#
# Why a second container:
#   The stock `worldvista/vehu` container runs ~148 M processes (TaskMan
#   submanagers, rocto, two %ydbgui instances, HL7/RPC/VistaLink socat listeners)
#   against the same region files. Those processes touch the read set of the
#   verifier's TP frame, and YottaDB SILENTLY RESTARTS the transaction: the body
#   re-executes, device output duplicates, and locals not named in TSTART(...)
#   are not restored. Measured on this machine, 20 one-second TP frames over a
#   read set containing ^%ZTSCH/^%ZTSK/^XTMP produced 58 restarts (max 3 per
#   frame) in `vehu` and 0 restarts in the quiesced container.
#
#   So: build `rosetta-verify` from the SAME image with the /start.sh entrypoint
#   replaced by an idle loop. Nothing but our own worker ever opens the region.
#   The stock `vehu` container is never modified or stopped by this script.
#
# Idempotent. Safe to re-run. Exits non-zero on any failure.
#
# Usage:
#   scripts/bootstrap.sh                 # create/verify the container
#   scripts/bootstrap.sh --recreate      # tear down and rebuild it
#   scripts/bootstrap.sh --measure       # additionally run the restart benchmark
#   scripts/bootstrap.sh --report-only   # just print the discovered region paths

set -euo pipefail

IMAGE="${ROSETTA_IMAGE:-worldvista/vehu}"
CONTAINER="${ROSETTA_CONTAINER:-rosetta-verify}"
PLATFORM="${ROSETTA_PLATFORM:-linux/amd64}"
INSTANCE="${ROSETTA_INSTANCE:-vehu}"
BASEDIR="/home/${INSTANCE}"
LIVE_CONTAINER="${ROSETTA_LIVE_CONTAINER:-vehu}"

RECREATE=0
MEASURE=0
REPORT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --recreate)    RECREATE=1 ;;
    --measure)     MEASURE=1 ;;
    --report-only) REPORT_ONLY=1 ;;
    -h|--help)     sed -n '1,30p' "$0"; exit 0 ;;
    *) echo "bootstrap: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# In-container command as the VistA instance user with the M environment sourced.
mx() { docker exec -u "$INSTANCE" "$CONTAINER" bash -c "source ${BASEDIR}/etc/env && $1"; }

# ---------------------------------------------------------------- prerequisites

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker info >/dev/null 2>&1 || die "docker daemon not reachable"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  say "Pulling $IMAGE (multi-GB; on Apple Silicon this runs under emulation)"
  docker pull --platform "$PLATFORM" "$IMAGE"
fi

# ------------------------------------------------------------ container lifecycle

container_state() { docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "absent"; }

if [ "$RECREATE" = 1 ]; then
  say "Removing existing $CONTAINER (--recreate)"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
fi

if [ "$REPORT_ONLY" = 0 ]; then
  state="$(container_state)"
  case "$state" in
    running)
      say "Container $CONTAINER already running -- reusing"
      ;;
    exited|created|paused)
      say "Container $CONTAINER exists ($state) -- starting"
      docker start "$CONTAINER" >/dev/null
      ;;
    absent)
      say "Creating quiesced container $CONTAINER from $IMAGE"
      # No published ports: nothing can connect in, so no socat-spawned M jobs.
      # Entrypoint replaced so /start.sh (TaskMan, rocto, %ydbgui, socat) never runs.
      docker run -d \
        --name "$CONTAINER" \
        --platform "$PLATFORM" \
        --entrypoint /bin/bash \
        "$IMAGE" \
        -c 'trap "exit 0" TERM INT; while true; do sleep 3600 & wait $!; done' >/dev/null
      ;;
    *)
      die "container $CONTAINER in unexpected state: $state"
      ;;
  esac

  # Wait for the container to accept exec.
  for _ in $(seq 1 30); do
    if docker exec "$CONTAINER" true >/dev/null 2>&1; then break; fi
    sleep 1
  done
  docker exec "$CONTAINER" true >/dev/null 2>&1 || die "container $CONTAINER never became execable"
fi

[ "$(container_state)" = "running" ] || die "container $CONTAINER is not running"

# ------------------------------------------------------------------- quiescence

say "Verifying quiescence"
# Anything that opens the region and is not ours is a restart source. The stock
# image starts these from /start.sh; with the entrypoint replaced none should exist.
NOISE_PATTERN='mumps -direct|ZTMB|rocto|ydbgui|socat|sshd|webreq'
noise="$(docker exec "$CONTAINER" ps -eo pid,args --no-headers 2>/dev/null \
          | grep -vE '\[.*\] <defunct>' \
          | grep -E "$NOISE_PATTERN" || true)"
if [ -n "$noise" ]; then
  warn "background processes still present in $CONTAINER:"
  printf '%s\n' "$noise" >&2
  warn "attempting to stop them"
  docker exec -u root "$CONTAINER" bash -c '
    /etc/init.d/vehuvista stop >/dev/null 2>&1 || true
    [ -f /etc/init.d/vehuvista-ydbgui ] && /etc/init.d/vehuvista-ydbgui stop >/dev/null 2>&1 || true
    pkill -f "mumps -direct"  >/dev/null 2>&1 || true
    pkill -f rocto            >/dev/null 2>&1 || true
    pkill -f ydbgui           >/dev/null 2>&1 || true
    pkill -f "socat TCP-LISTEN" >/dev/null 2>&1 || true
    true'
  sleep 2
  noise="$(docker exec "$CONTAINER" ps -eo pid,args --no-headers 2>/dev/null \
            | grep -vE '\[.*\] <defunct>' \
            | grep -E "$NOISE_PATTERN" || true)"
  [ -z "$noise" ] || die "could not quiesce $CONTAINER; still running:
$noise"
fi
live_procs="$(docker exec "$CONTAINER" ps -eo pid --no-headers 2>/dev/null | wc -l | tr -d ' ')"
say "Quiescent: $live_procs process(es) in $CONTAINER (none of them M)"

# --------------------------------------------------------------- region hunting
# "Without those paths there is no snapshot/restore and no project." -- PROJECT.md #8

say "Hunting YottaDB region files"
GLD="$(mx 'echo "$gtmgbldir"' | tr -d '\r')"
GTM_DIST="$(mx 'echo "$gtm_dist"' | tr -d '\r')"
[ -n "$GLD" ]      || die "gtmgbldir is empty in ${BASEDIR}/etc/env"
[ -n "$GTM_DIST" ] || die "gtm_dist is empty in ${BASEDIR}/etc/env"
mx "test -x \$gtm_dist/mumps" || die "no mumps binary at $GTM_DIST/mumps"

# Authoritative mapping comes out of the global directory, not from guessing.
# `GDE show -segment` prints "<SEGMENT> <path>.dat" lines; everything else in
# that report is indented option text, so require an unindented first column.
REGION_TSV="$(mx 'cd '"${BASEDIR}"'/tmp && echo "show -segment" | $gtm_dist/mumps -run GDE 2>/dev/null' \
              | tr -d '\r' \
              | awk '$0 ~ /^ [A-Za-z0-9_]+ +\// {print $1"\t"$2}' || true)"

if [ -z "$REGION_TSV" ]; then
  # Last resort: the .dat files on disk. Always correct for this image layout.
  REGION_TSV="$(mx 'for f in '"${BASEDIR}"'/g/*.dat; do
                      printf "%s\t%s\n" "$(basename "${f%.dat}" | tr a-z A-Z)" "$f"
                    done' | tr -d '\r')"
fi
[ -n "$REGION_TSV" ] || die "could not discover any .dat region files"

say "Region map (global directory: $GLD)"
printf '%s\n' "$REGION_TSV" | while IFS=$'\t' read -r region datfile; do
  [ -n "$datfile" ] || continue
  size="$(docker exec "$CONTAINER" stat -c '%s' "$datfile" 2>/dev/null || echo 0)"
  human="$(awk -v b="$size" 'BEGIN{printf "%.2f GB", b/1073741824}')"
  printf '    %-10s %-40s %s\n' "$region" "$datfile" "$human"
done

# Machine-readable manifest for rosetta.core. Written into the container so the
# runtime can read it without re-running discovery; also echoed to stdout.
MANIFEST="${BASEDIR}/tmp/rosetta-regions.tsv"
printf '%s\n' "$REGION_TSV" | docker exec -i -u "$INSTANCE" "$CONTAINER" \
  bash -c "cat > '$MANIFEST'"
say "Region manifest written to $CONTAINER:$MANIFEST"

# Scratch directories used by snapshot()/restore() and by load_routine().
mx "mkdir -p ${BASEDIR}/tmp/rosetta ${BASEDIR}/tmp/rosetta/snapshots" >/dev/null

# ------------------------------------------------------------------ smoke test

say "Smoke test: driving a real routine"
UEI_OUT="$(mx 'cd '"${BASEDIR}"'/tmp && printf "W \"UEI=\",\$\$VALIDUEI^PRCHUEI(\"ZQGGH7C1MJM3\"),!\nW \"CRC=\",\$\$CRC32^XLFCRC(\"hello\"),!\nH\n" | $gtm_dist/mumps -direct' 2>&1 | tr -d '\r')"
echo "$UEI_OUT" | grep -q 'UEI=1' || die "smoke test failed: \$\$VALIDUEI^PRCHUEI did not return 1
$UEI_OUT"
echo "$UEI_OUT" | grep -q 'CRC=907060870' || warn "\$\$CRC32^XLFCRC(\"hello\") != 907060870 (expected per PROJECT.md)"
say "Smoke test passed"

# ------------------------------------------------- optional restart measurement

if [ "$MEASURE" = 1 ]; then
  say "Measuring TP restart incidence (quiesced vs live)"
  probe="$(mktemp -t rosprobe)"
  cat > "$probe" <<'MPROBE'
ROSBOOT ; bootstrap restart probe -- long-held TP frame over a hot read set
HOLD(N,SEC) ;
 N I,TOT,MAXR,R,V,K,J
 S TOT=0,MAXR=0
 F I=1:1:N D
 . TSTART ():SERIAL
 . S V=$G(^%ZTSCH("TASK")),V=$G(^%ZTSK(0)),V=$G(^%ZTSCH)
 . S K="" F J=1:1:50 S K=$O(^%ZTSK(K)) Q:K=""  S V=$G(^%ZTSK(K,0))
 . S K="" F J=1:1:50 S K=$O(^XTMP(K)) Q:K=""  S V=$G(^XTMP(K,0))
 . H SEC
 . S V=$G(^%ZTSCH("TASK"))
 . S ^ROSTMP("bootprobe",I)=$ZUT
 . S R=$TRESTART S TOT=TOT+R S:R>MAXR MAXR=R
 . TROLLBACK
 W "restarts=",TOT," max=",MAXR,!
 Q
MPROBE
  for c in "$CONTAINER" "$LIVE_CONTAINER"; do
    docker inspect -f '{{.State.Status}}' "$c" >/dev/null 2>&1 || { warn "skip $c (absent)"; continue; }
    [ "$(docker inspect -f '{{.State.Status}}' "$c")" = running ] || { warn "skip $c (not running)"; continue; }
    docker cp "$probe" "$c:${BASEDIR}/r/ROSBOOT.m" >/dev/null
    docker exec -u root "$c" chown "${INSTANCE}:${INSTANCE}" "${BASEDIR}/r/ROSBOOT.m"
    out="$(docker exec -u "$INSTANCE" "$c" bash -c \
      "source ${BASEDIR}/etc/env && cd ${BASEDIR}/tmp && printf 'D HOLD^ROSBOOT(20,1)\nH\n' | \$gtm_dist/mumps -direct" \
      2>&1 | grep 'restarts=' | tr -d '\r' || echo 'restarts=? max=?')"
    printf '    %-16s 20 x 1s TP frames -> %s\n' "$c" "$out"
    docker exec -u root "$c" rm -f "${BASEDIR}/r/ROSBOOT.m" "${BASEDIR}/r/ROSBOOT.o" 2>/dev/null || true
  done
  rm -f "$probe"
  echo
  echo "    Interpretation: any nonzero 'restarts' in a container means the TP"
  echo "    body re-executed there. Use $CONTAINER, not $LIVE_CONTAINER."
fi

echo
say "Ready."
echo "    container : $CONTAINER  (image $IMAGE, platform $PLATFORM)"
echo "    gtm_dist  : $GTM_DIST"
echo "    gtmgbldir : $GLD"
echo "    mumps     : $GTM_DIST/mumps"
echo "    routines  : ${BASEDIR}/r"
echo "    manifest  : $MANIFEST"
echo
echo "    Next: python -m rosetta.core.selftest"
