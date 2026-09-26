#!/bin/bash
# Port-aware copy of write_test.sh for the multi-board bench (Phase 44 session).
# Usage: write_test_port.sh <PORT> [EPROM] [LABEL]
# Same pattern set as write_test.sh: null / 0xFF / random / partial low+high,
# each write -> verify -> read-back -> compare. Adds global -p <PORT> and -f.
set -u
PORT=${1:?need port}
EPROM_NAME=${2:-W27C512}
LABEL=${3:-$(basename "$PORT")}
DEFAULT_ARGS="-f"            # seated chips report 0xda01; force past ID/VPP check (VPP validated 12.2V)
JSON_FILE='./firestarter/data/chip_database.json'
TEMP_DIR="./test_data_${LABEL}"
mkdir -p "$TEMP_DIR"; rm -f "$TEMP_DIR"/*
EPROM_NAME=$(echo "$EPROM_NAME" | tr '[:lower:]' '[:upper:]')

MEMORY_SIZE_HEX=$(jq -e --arg t "$EPROM_NAME" -r '.[]|.[]|select((.part_number|split(",")|index($t)) != null)|.electrical.size_bytes' "$JSON_FILE")
[ -z "$MEMORY_SIZE_HEX" ] && { echo "No DB match for $EPROM_NAME"; exit 1; }
MEM_SIZE=$((MEMORY_SIZE_HEX)); HALF_SIZE=$((MEM_SIZE/2))
echo "=== WRITE TEST on $LABEL ($PORT), chip=$EPROM_NAME size=$MEMORY_SIZE_HEX ==="

dd if=/dev/urandom of="$TEMP_DIR/low_data.bin"  bs=1 count=$HALF_SIZE status=none
dd if=/dev/urandom of="$TEMP_DIR/high_data.bin" bs=1 count=$HALF_SIZE status=none
dd if=/dev/zero    of="$TEMP_DIR/null.bin" bs=$MEM_SIZE count=1 status=none
tr </dev/zero '\000' '\377' | head -c $MEM_SIZE >"$TEMP_DIR/0xFF.bin"
cat "$TEMP_DIR/low_data.bin" "$TEMP_DIR/high_data.bin" >"$TEMP_DIR/full_data.bin"

exec_fs() { # TEST CMD EPROM FILE [EXTRA]
  local tn=$1 cmd=$2 ep=$3 file=$4 extra=${5:-}
  local fc="firestarter -p $PORT $cmd $DEFAULT_ARGS $extra $ep $TEMP_DIR/$file"
  echo "--- $tn : $fc"
  $fc || { echo "$tn FAILED (exit $?)"; return 1; }
  echo
}
compare() {
  if diff --suppress-common-lines -y <(xxd "$TEMP_DIR/$1") <(xxd "$TEMP_DIR/$2") >/dev/null; then
    echo "  ✓ read-back matches $1"
  else
    echo "  ✗ MISMATCH: read-back != $1"
  fi
}
rwt() { # FILE [NAME]
  local f=$1
  local n=${2:-$(basename "$f" .bin)}
  exec_fs "Write $n"  write  "$EPROM_NAME" "$f"          || return 1
  exec_fs "Verify $n" verify "$EPROM_NAME" "$f"          || true
  exec_fs "Read $n"   read   "$EPROM_NAME" "read_back.bin" || true
  compare "$f" "read_back.bin"
}

rwt null.bin
rwt 0xFF.bin
rwt full_data.bin "random"
echo "--- partial writes ---"
exec_fs "Write low"  write "$EPROM_NAME" low_data.bin || true
exec_fs "Write high" write "$EPROM_NAME" high_data.bin "-b -a $HALF_SIZE" || true
exec_fs "Verify low"  verify "$EPROM_NAME" low_data.bin || true
echo "=== WRITE TEST on $LABEL complete ==="
