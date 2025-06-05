#!/bin/bash
#
# ----------------------------------------------------------
#
# Project Name: Firestarter
# Copyright (c) 2025 Henrik Olsson
#
# Permission is hereby granted under MIT license.
#
# ----------------------------------------------------------
#
EPROM_NAME=${1:-W27C512}

# DEFAULT_ARGS="-f"
VERBOSE=0

JSON_FILE='./firestarter/data/database_generated.json'
TEMP_DIR="./test_data"

# Ensure the temp directory exists, if not create it
if [ ! -d "$TEMP_DIR" ]; then
    mkdir -p "$TEMP_DIR"
    echo "Temporary directory created: $TEMP_DIR"
fi

# Clean up any existing files in the temp directory from previous runs
rm -f "$TEMP_DIR"/*

# Trap to clean up the temporary files on exit or interrupt
# trap "rm -rf $TEMP_DIR; echo 'Cleaned up temp files'; exit" EXIT

# Convert TARGET_NAME to uppercase
EPROM_NAME=$(echo "$EPROM_NAME" | tr '[:lower:]' '[:upper:]')

# Use jq to parse JSON and match name, retrieving the corresponding memory-size
MEMORY_SIZE_HEX=$(jq -e --arg target_name "$EPROM_NAME" -r '
  .[] | 
  .[] | 
  select(.name == $target_name) | 
  .["memory-size"]
' "$JSON_FILE")

# Exit if no match is found (MEMORY_SIZE_HEX will be null if no match)
if [ -z "$MEMORY_SIZE_HEX" ]; then
    echo "Error: No match found for name '$EPROM_NAME' in the JSON file."
    exit 1
fi

# Convert the hex value (e.g., 0x40000) to decimal
MEM_SIZE=$((MEMORY_SIZE_HEX))

# Output the result or use it further in your script
echo "Memory size: $MEMORY_SIZE_HEX"

HALF_SIZE=$((MEM_SIZE / 2))

# Generate two random files in the temp directory, each with size HALF_SIZE
dd if=/dev/urandom of="$TEMP_DIR/low_data.bin" bs=1 count=$HALF_SIZE status=none
dd if=/dev/urandom of="$TEMP_DIR/high_data.bin" bs=1 count=$HALF_SIZE status=none
dd if=/dev/zero of="$TEMP_DIR/null.bin" bs=$MEM_SIZE count=1 status=none
tr </dev/zero '\000' '\377' | head -c $MEM_SIZE >$TEMP_DIR/0xFF.bin

dd if=/dev/zero of="$TEMP_DIR/null_1k.bin" bs=1024 count=1 status=none
tr </dev/zero '\000' '\377' | head -c 1024 >"$TEMP_DIR/0xFF_1k.bin"

# Concatenate the two files into one file
cat "$TEMP_DIR/low_data.bin" "$TEMP_DIR/high_data.bin" >"$TEMP_DIR/full_data.bin"

exec_firestarter() {
    TEST_NAME=$1
    CMD_NAME=$2
    EPROM_NAME=$3
    CMD_ARGS=${5:-}
    if test $VERBOSE -eq 1; then
        VERBOSE_FLAG="-v"
    fi
    firestarter_cmd="firestarter $VERBOSE_FLAG $CMD_NAME $DEFAULT_ARGS $CMD_ARGS $EPROM_NAME $TEMP_DIR/$4"
    echo "---------------------------------"
    echo "Test: $TEST_NAME - $EPROM_NAME"
    echo "Cmd: $firestarter_cmd"
    echo "---------------------------------"
    $firestarter_cmd
    if test $? -gt 0; then
        echo "$TEST_NAME failed"
        exit 1
    fi
    echo
    sleep 0.5

}

compare_files() {
    diff --suppress-common-lines -y <(xxd "$TEMP_DIR/$1") <(xxd "$TEMP_DIR/$2")
    if test $? -gt 0; then
        echo "Read back data does not match"
        exit 1
    fi
    echo "Files are identical"
    echo
    sleep 0.5
}

read_write_test() {
    TEST_FILE=$1
    TEST_NAME=${2:-$(basename $TEST_FILE .bin)}
    exec_firestarter "Writing $TEST_NAME" write $EPROM_NAME "$TEST_FILE"
    exec_firestarter "Verifying $TEST_NAME" verify $EPROM_NAME "$TEST_FILE"
    exec_firestarter "Reading $TEST_NAME" read $EPROM_NAME "read_back.bin"
    compare_files $TEST_FILE "read_back.bin"
}

read_write_test null.bin
read_write_test 0xFF.bin
read_write_test full_data.bin "random data"

exec_firestarter "Writing - low part" write $EPROM_NAME low_data.bin
exec_firestarter "Writing - high part" write $EPROM_NAME high_data.bin "-b -a $HALF_SIZE"

exec_firestarter "Verifying - low part" verify $EPROM_NAME low_data.bin
exec_firestarter "Verifying - high part" verify $EPROM_NAME high_data.bin  "-a $HALF_SIZE"

exec_firestarter "Reading" read $EPROM_NAME read_back.bin 

compare_files "full_data.bin" "read_back.bin"
echo "All tests passed"
